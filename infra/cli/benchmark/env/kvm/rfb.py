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


# --- RFB security type 2 (VNC Authentication, DES challenge-response) ---------
#
# Needed ONLY for the Apple Virtualization.framework native VNC server
# (`_VZVNCServer`, exposed by `tart run --vnc-experimental`), which offers
# *only* security type 2. QEMU's VNC offers type 1 (None) and never reaches
# this code. The auth is a 16-byte DES challenge encrypted with the password as
# the key, but with each key byte's bits reversed (the VNC quirk). Pure stdlib —
# keeps rfb.py dependency-free. Validated against DES known-answer vectors and
# against the live Apple server in W0b (SecurityResult: 0).

# DES tables (FIPS 46-3). Compact; used only by _vnc_des_encrypt.
_DES_IP = [
    58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
]
_DES_FP = [
    40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25,
]
_DES_E = [
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13, 12, 13, 14, 15,
    16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25, 24, 25, 26, 27, 28,
    29, 28, 29, 30, 31, 32, 1,
]
_DES_P = [
    16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
    2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25,
]
_DES_PC1 = [
    57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18, 10, 2, 59, 51, 43, 35,
    27, 19, 11, 3, 60, 52, 44, 36, 63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38,
    30, 22, 14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4,
]
_DES_PC2 = [
    14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10, 23, 19, 12, 4, 26, 8, 16, 7, 27,
    20, 13, 2, 41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48, 44, 49, 39, 56,
    34, 53, 46, 42, 50, 36, 29, 32,
]
_DES_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
_DES_SBOX = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7, 0, 15, 7, 4, 14, 2,
     13, 1, 10, 6, 12, 11, 9, 5, 3, 8, 4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7,
     3, 10, 5, 0, 15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10, 3, 13, 4, 7, 15, 2,
     8, 14, 12, 0, 1, 10, 6, 9, 11, 5, 0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6,
     9, 3, 2, 15, 13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8, 13, 7, 0, 9, 3, 4, 6,
     10, 2, 8, 5, 14, 12, 11, 15, 1, 13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5,
     10, 14, 7, 1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15, 13, 8, 11, 5, 6, 15,
     0, 3, 4, 7, 2, 12, 1, 10, 14, 9, 10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14,
     5, 2, 8, 4, 3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9, 14, 11, 2, 12, 4, 7,
     13, 1, 5, 0, 15, 10, 3, 9, 8, 6, 4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5,
     6, 3, 0, 14, 11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11, 10, 15, 4, 2, 7, 12,
     9, 5, 6, 1, 13, 14, 0, 11, 3, 8, 9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1,
     13, 11, 6, 4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1, 13, 0, 11, 7, 4, 9, 1,
     10, 14, 3, 5, 12, 2, 15, 8, 6, 1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0,
     5, 9, 2, 6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7, 1, 15, 13, 8, 10, 3,
     7, 4, 12, 5, 6, 11, 0, 14, 9, 2, 7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13,
     15, 3, 5, 8, 2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _bits(data: bytes) -> list[int]:
    out = []
    for byte in data:
        for i in range(7, -1, -1):
            out.append((byte >> i) & 1)
    return out


def _frombits(bits: list[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for b in bits[i:i + 8]:
            byte = (byte << 1) | b
        out.append(byte)
    return bytes(out)


def _permute(block: list[int], table: list[int]) -> list[int]:
    return [block[i - 1] for i in table]


def _des_subkeys(key: bytes) -> list[list[int]]:
    key_bits = _bits(key)
    cd = _permute(key_bits, _DES_PC1)
    c, d = cd[:28], cd[28:]
    subkeys = []
    for shift in _DES_SHIFTS:
        c = c[shift:] + c[:shift]
        d = d[shift:] + d[:shift]
        subkeys.append(_permute(c + d, _DES_PC2))
    return subkeys


def _des_encrypt_block(block8: bytes, key8: bytes) -> bytes:
    subkeys = _des_subkeys(key8)
    bits = _permute(_bits(block8), _DES_IP)
    left, right = bits[:32], bits[32:]
    for k in subkeys:
        er = _permute(right, _DES_E)
        x = [er[i] ^ k[i] for i in range(48)]
        out = []
        for j in range(8):
            chunk = x[j * 6:j * 6 + 6]
            row = (chunk[0] << 1) | chunk[5]
            col = (chunk[1] << 3) | (chunk[2] << 2) | (chunk[3] << 1) | chunk[4]
            val = _DES_SBOX[j][row * 16 + col]
            out.extend([(val >> 3) & 1, (val >> 2) & 1, (val >> 1) & 1, val & 1])
        f = _permute(out, _DES_P)
        new_right = [left[i] ^ f[i] for i in range(32)]
        left, right = right, new_right
    return _frombits(_permute(right + left, _DES_FP))


def _vnc_des_response(password: str, challenge: bytes) -> bytes:
    """16-byte VNC-auth response: DES-ECB encrypt the 16-byte challenge with the
    password (NUL-padded/truncated to 8 bytes, each byte bit-reversed) as key."""
    pw = (password.encode("latin-1") + b"\x00" * 8)[:8]
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in pw)  # reverse bits in each byte
    return _des_encrypt_block(challenge[:8], key) + _des_encrypt_block(challenge[8:16], key)


class RfbError(RuntimeError):
    pass


class RfbClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 30.0,
        vnc_password: str | None = None,
    ):
        # vnc_password: when set, enables RFB security type 2 (VNC-DES) auth and
        # advertises the DesktopSize pseudo-encoding the Apple VZ VNC server
        # mandates. When None (the QEMU/KVM default), behaviour is byte-for-byte
        # the original type-1 (None) + [Raw] path — KVM is unaffected.
        self.host = host
        self.port = port
        self.vnc_password = vnc_password
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
        if self.vnc_password is not None and 2 in types and 1 not in types:
            # Apple VZ native VNC: only security type 2 (VNC-DES) is offered.
            self.s.sendall(bytes([2]))  # select VNC Authentication
            challenge = self._recv_exact(16)
            self.s.sendall(_vnc_des_response(self.vnc_password, challenge))
        elif 1 in types:
            self.s.sendall(bytes([1]))  # security type None (QEMU/KVM path)
        else:
            raise RfbError(f"server requires VNC auth (security types={list(types)})")
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
        if self.vnc_password is not None:
            # Apple VZ native VNC tears down its own listener unless the client
            # advertises DesktopSize (-223). With it present the server serves
            # plain Raw (0). We deliberately do NOT advertise Cursor (-239): that
            # invites data-bearing cursor-shape pseudo-rects (and the VZ server
            # also emits other private pseudo-encodings) which would desync our
            # Raw-only reader. [Raw, DesktopSize] is the exact set W0b validated.
            encs = [0, -223]
        else:
            encs = [0]  # QEMU/KVM: Raw only — unchanged
        self.s.sendall(
            struct.pack(">BxH", 2, len(encs)) + b"".join(struct.pack(">i", e) for e in encs)
        )  # SetEncodings
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
            elif enc == -223:  # DesktopSize: framebuffer resized to rw x rh, no payload.
                if (rw, rh) != (self.w, self.h):
                    self.w, self.h = rw, rh
                    canvas = canvas.resize((self.w, self.h))
                continue
            elif enc == -239:  # Cursor pseudo-rect: carries pixels + a 1-bpp bitmask we
                # don't render, but MUST consume to stay byte-aligned. (Not advertised
                # for VZ, but tolerate it if a server sends it unsolicited.)
                self._recv_exact(rw * rh * 4 + ((rw + 7) // 8) * rh)
                continue
            else:
                # Unknown pseudo/encoding: we can't know its payload length, so the
                # stream is unrecoverable. Fail loud rather than desync silently.
                raise RfbError(f"unsupported encoding {enc} (advertised [Raw, DesktopSize])")
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
