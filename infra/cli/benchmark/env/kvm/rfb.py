"""Raw-socket RFB 3.8 client for driving + screenshotting a QEMU VNC guest.

One TCP connection serves both directions:
  - screenshot(): SetPixelFormat(32bpp) + SetEncodings([Raw]) + a non-incremental
    FramebufferUpdateRequest, then decode the Raw rectangles into a PIL.Image.
  - key / type / pointer: KeyEvent (msg 4) + PointerEvent (msg 5).

No dependencies beyond stdlib + PIL. Ported and extended from the spike's
~/workspace/kvm-spike/scripts/vnc_drive.py (which only did input).
"""
from __future__ import annotations

import socket
import struct
import time

from PIL import Image

# --- X11 keysyms we map by name (lowercased lookup) ---
_NAMED_KEYS: dict[str, int] = {
    "return": 0xFF0D, "enter": 0xFF0D,
    "escape": 0xFF1B, "esc": 0xFF1B,
    "tab": 0xFF09,
    "backspace": 0xFF08,
    "delete": 0xFFFF, "forward_delete": 0xFFFF,
    "space": 0x0020,
    "left": 0xFF51, "up": 0xFF52, "right": 0xFF53, "down": 0xFF54,
    "home": 0xFF50, "end": 0xFF57,
    "page_up": 0xFF55, "pageup": 0xFF55, "prior": 0xFF55,
    "page_down": 0xFF56, "pagedown": 0xFF56, "next": 0xFF56,
    "insert": 0xFF63,
    "caps_lock": 0xFFE5,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},  # F1..F12
}

# Modifier names -> keysym. NOTE: on the macOS guest, the host Super key is bound
# to Command in QEMU's default keymap. If cmd shortcuts misfire, flip CMD_KEYSYM
# to 0xFFE7 (Meta_L) — both are kept here so it's a one-line change.
_SUPER_L = 0xFFEB
_META_L = 0xFFE7
CMD_KEYSYM = _SUPER_L

_MODIFIERS: dict[str, int] = {
    "shift": 0xFFE1,
    "ctrl": 0xFFE3, "control": 0xFFE3,
    "alt": 0xFFE9, "option": 0xFFE9, "opt": 0xFFE9,
    "cmd": CMD_KEYSYM, "command": CMD_KEYSYM, "super": CMD_KEYSYM,
    "meta": CMD_KEYSYM, "win": CMD_KEYSYM,
}

# Characters that require Shift held to produce on a US keyboard.
_SHIFT_CHARS = set('~!@#$%^&*()_+{}|:"<>?')
_SHIFT_SYM = 0xFFE1

# Pointer button-mask bits (RFB PointerEvent).
_BTN = {"left": 1 << 0, "middle": 1 << 1, "right": 1 << 2}
_SCROLL = {"up": 1 << 3, "down": 1 << 4, "left": 1 << 5, "right": 1 << 6}


def _char_keysym(ch: str) -> int:
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return o
    if ch == "\n":
        return 0xFF0D
    if ch == "\t":
        return 0xFF09
    return o  # best effort


class RfbError(RuntimeError):
    pass


class RfbClient:
    def __init__(self, host: str, port: int, *, timeout: float = 30.0):
        self.host = host
        self.port = port
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.settimeout(timeout)
        self.w = 0
        self.h = 0
        self.name = ""
        self._buttons = 0  # currently-held pointer button mask
        self._x = 0
        self._y = 0
        self._pixel_format_set = False
        self._handshake()

    # --- low level ---

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise RfbError(f"connection closed mid-read ({len(buf)}/{n} bytes)")
            buf.extend(chunk)
        return bytes(buf)

    def _handshake(self) -> None:
        greeting = self._recv_exact(12)
        if not greeting.startswith(b"RFB "):
            raise RfbError(f"bad RFB greeting: {greeting!r}")
        self.s.sendall(b"RFB 003.008\n")
        n = self._recv_exact(1)[0]
        if n == 0:
            reason_len = struct.unpack(">I", self._recv_exact(4))[0]
            reason = self._recv_exact(reason_len).decode("ascii", "replace")
            raise RfbError(f"server refused connection: {reason}")
        types = self._recv_exact(n)
        if 1 not in types:
            raise RfbError(f"server requires VNC auth (security types={list(types)})")
        self.s.sendall(bytes([1]))  # security type None
        result = struct.unpack(">I", self._recv_exact(4))[0]
        if result != 0:
            raise RfbError(f"security handshake failed: {result}")
        self.s.sendall(bytes([1]))  # ClientInit, shared=1
        hdr = self._recv_exact(24)
        self.w, self.h = struct.unpack(">HH", hdr[:4])
        name_len = struct.unpack(">I", hdr[20:24])[0]
        self.name = self._recv_exact(name_len).decode("ascii", "replace")
        self._x, self._y = self.w // 2, self.h // 2

    def _set_pixel_format(self) -> None:
        # 32 bpp, depth 24, little-endian, true-color. Shifts R=16/G=8/B=0 -> bytes
        # are laid out B,G,R,X (little-endian), i.e. PIL raw mode "BGRX".
        pf = struct.pack(
            ">BBBBHHHBBBxxx",
            32,    # bits-per-pixel
            24,    # depth
            0,     # big-endian-flag
            1,     # true-color-flag
            255,   # red-max
            255,   # green-max
            255,   # blue-max
            16,    # red-shift
            8,     # green-shift
            0,     # blue-shift
        )
        self.s.sendall(struct.pack(">Bxxx", 0) + pf)  # SetPixelFormat (msg 0)
        self.s.sendall(struct.pack(">BxH", 2, 1) + struct.pack(">i", 0))  # SetEncodings([Raw])
        self._pixel_format_set = True

    # --- screenshot ---

    def screenshot(self) -> Image.Image:
        if not self._pixel_format_set:
            self._set_pixel_format()
        # Non-incremental full-screen update request.
        self.s.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, self.w, self.h))
        canvas = Image.new("RGB", (self.w, self.h))
        msg_type = self._read_until_framebuffer_update()
        if msg_type != 0:
            raise RfbError(f"expected FramebufferUpdate, got msg {msg_type}")
        self._recv_exact(1)  # padding
        n_rects = struct.unpack(">H", self._recv_exact(2))[0]
        for _ in range(n_rects):
            rx, ry, rw, rh, enc = struct.unpack(">HHHHi", self._recv_exact(12))
            if enc == 0:  # Raw
                data = self._recv_exact(rw * rh * 4)
                tile = Image.frombuffer("RGB", (rw, rh), data, "raw", "BGRX", 0, 1)
                canvas.paste(tile, (rx, ry))
            elif enc == -239 or enc == -223:  # Cursor / DesktopSize pseudo: no data we need
                continue
            else:
                raise RfbError(f"unsupported encoding {enc} (advertised Raw only)")
        return canvas

    def _read_until_framebuffer_update(self) -> int:
        """Consume Bell/CutText/ColourMap messages until a FramebufferUpdate header."""
        for _ in range(16):
            msg_type = self._recv_exact(1)[0]
            if msg_type == 0:
                return 0
            if msg_type == 1:  # SetColourMapEntries
                self._recv_exact(3)
                first, count = struct.unpack(">HH", self._recv_exact(4))
                self._recv_exact(count * 6)
            elif msg_type == 2:  # Bell
                pass
            elif msg_type == 3:  # ServerCutText
                self._recv_exact(3)
                length = struct.unpack(">I", self._recv_exact(4))[0]
                self._recv_exact(length)
            else:
                raise RfbError(f"unexpected server message type {msg_type}")
        raise RfbError("no FramebufferUpdate after 16 server messages")

    # --- keyboard ---

    def _key(self, sym: int, down: bool) -> None:
        self.s.sendall(struct.pack(">BBHI", 4, 1 if down else 0, 0, sym))

    def _tap(self, sym: int) -> None:
        self._key(sym, True)
        time.sleep(0.02)
        self._key(sym, False)
        time.sleep(0.03)

    def type_text(self, text: str) -> None:
        for ch in text:
            sym = _char_keysym(ch)
            shift = ch.isupper() or ch in _SHIFT_CHARS
            if shift:
                self._key(_SHIFT_SYM, True)
            self._tap(sym)
            if shift:
                self._key(_SHIFT_SYM, False)
            time.sleep(0.01)

    def key_combo(self, spec: str) -> None:
        """Press a key spec like 'cmd+s', 'ctrl+shift+4', 'Return', 'escape'."""
        tokens = [t for t in spec.split("+") if t]
        if not tokens:
            return
        mods: list[int] = []
        final: int | None = None
        for tok in tokens:
            low = tok.lower()
            if low in _MODIFIERS:
                mods.append(_MODIFIERS[low])
            elif low in _NAMED_KEYS:
                final = _NAMED_KEYS[low]
            elif len(tok) == 1:
                final = _char_keysym(tok.lower())
            else:
                # Unknown multi-char token: treat as a named key best-effort.
                final = _NAMED_KEYS.get(low, _char_keysym(tok[0]))
        for m in mods:
            self._key(m, True)
        time.sleep(0.03)
        if final is not None:
            self._tap(final)
        for m in reversed(mods):
            self._key(m, False)
        time.sleep(0.04)

    def hold_key(self, spec: str, duration: float) -> None:
        tokens = [t for t in spec.split("+") if t]
        syms = [
            _MODIFIERS.get(t.lower())
            or _NAMED_KEYS.get(t.lower())
            or _char_keysym(t.lower()[0])
            for t in tokens
        ]
        for sym in syms:
            self._key(sym, True)
        time.sleep(max(0.0, duration))
        for sym in reversed(syms):
            self._key(sym, False)

    # --- pointer ---

    def _send_pointer(self, mask: int, x: int, y: int) -> None:
        x = max(0, min(int(x), self.w - 1))
        y = max(0, min(int(y), self.h - 1))
        self._x, self._y = x, y
        self.s.sendall(struct.pack(">BBHH", 5, mask & 0xFF, x, y))

    def move(self, x: int, y: int) -> None:
        self._send_pointer(self._buttons, x, y)
        time.sleep(0.02)

    def button_down(self, button: str = "left") -> None:
        self._buttons |= _BTN.get(button, 1)
        self._send_pointer(self._buttons, self._x, self._y)
        time.sleep(0.03)

    def button_up(self, button: str = "left") -> None:
        self._buttons &= ~_BTN.get(button, 1)
        self._send_pointer(self._buttons, self._x, self._y)
        time.sleep(0.03)

    def click(self, x: int, y: int, button: str = "left", *, double: bool = False) -> None:
        self.move(x, y)
        clicks = 2 if double else 1
        for i in range(clicks):
            self.button_down(button)
            self.button_up(button)
            if double and i == 0:
                time.sleep(0.08)

    def click_with_modifiers(self, x: int, y: int, button: str, modifier_spec: str) -> None:
        """Click while holding modifiers (e.g. 'shift', 'cmd+shift'). RFB supports this."""
        syms = [
            _MODIFIERS[t.lower()]
            for t in modifier_spec.split("+")
            if t and t.lower() in _MODIFIERS
        ]
        for sym in syms:
            self._key(sym, True)
        time.sleep(0.03)
        self.click(x, y, button)
        for sym in reversed(syms):
            self._key(sym, False)
        time.sleep(0.03)

    def drag(self, sx: int, sy: int, ex: int, ey: int, button: str = "left") -> None:
        self.move(sx, sy)
        self.button_down(button)
        time.sleep(0.05)
        self.move(ex, ey)
        time.sleep(0.05)
        self.button_up(button)

    def scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> None:
        self.move(x, y)
        bit = _SCROLL.get(direction, _SCROLL["down"])
        for _ in range(max(1, int(amount))):
            self._send_pointer(self._buttons | bit, x, y)
            time.sleep(0.02)
            self._send_pointer(self._buttons, x, y)
            time.sleep(0.02)

    def close(self) -> None:
        try:
            self.s.close()
        except OSError:
            pass
