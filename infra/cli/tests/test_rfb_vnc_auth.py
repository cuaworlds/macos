"""RFB security-type-2 (VNC-DES) auth + DesktopSize encoding for Apple VZ VNC.

The Apple Virtualization.framework native VNC server (`tart run --vnc-experimental`)
offers ONLY RFB security type 2 (VNC Authentication, DES challenge-response) and
tears down its own listener unless the client advertises the DesktopSize (-223)
pseudo-encoding in SetEncodings. QEMU/KVM offers type 1 (None) and never hits this.

These tests pin BOTH:
  * the DES core against FIPS 46-3 known-answer vectors, and
  * the gated wire behaviour: a vnc_password-less client is byte-for-byte the
    original type-1 None + [Raw] (KVM unchanged), and a vnc_password client does
    a verifiable type-2 DES handshake + advertises [Raw, DesktopSize, Cursor].
"""

from __future__ import annotations

import binascii
import socket
import struct
import threading

from benchmark.env.kvm.rfb import RfbClient, _des_encrypt_block, _vnc_des_response


def test_des_known_answer_vectors():
    # FIPS 46-3 test vector.
    ct = _des_encrypt_block(
        binascii.unhexlify("0123456789ABCDEF"), binascii.unhexlify("133457799BBCDFF1")
    )
    assert ct.hex() == "85e813540f0ab405"
    # All-zero key/plaintext.
    assert _des_encrypt_block(b"\x00" * 8, b"\x00" * 8).hex() == "8ca64de9c1b123a7"


def _serve(security_types, password=None):
    """Minimal RFB 3.8 server thread. Captures what the client sent."""
    cap: dict = {}
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]

    def run():
        c, _ = lsock.accept()
        try:
            c.sendall(b"RFB 003.008\n")
            c.recv(12)  # client version
            c.sendall(bytes([len(security_types)]) + bytes(security_types))
            sel = c.recv(1)[0]
            cap["selected_sec"] = sel
            if sel == 2:
                challenge = bytes(range(16))
                c.sendall(challenge)
                resp = c.recv(16)
                cap["des_ok"] = resp == _vnc_des_response(password, challenge)
            c.sendall(struct.pack(">I", 0))  # SecurityResult OK
            c.recv(1)  # ClientInit
            name = b"fake"
            c.sendall(
                struct.pack(">HH", 800, 600) + b"\x00" * 16
                + struct.pack(">I", len(name)) + name
            )
            c.recv(20)  # SetPixelFormat
            hdr = c.recv(4)  # SetEncodings header
            count = struct.unpack(">H", hdr[2:4])[0]
            cap["encodings"] = [struct.unpack(">i", c.recv(4))[0] for _ in range(count)]
        finally:
            c.close()
            lsock.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, port, cap


def test_kvm_path_unchanged_when_no_password():
    # Offer both types; the None (KVM) path must still select type 1 + advertise [Raw].
    t, port, cap = _serve([1, 2])
    cli = RfbClient("127.0.0.1", port)
    cli._set_pixel_format()
    t.join(timeout=5)
    assert cap["selected_sec"] == 1
    assert cap["encodings"] == [0]


def test_vz_path_des_auth_and_desktopsize():
    pw = "dignity-essay-equip-fortune"
    t, port, cap = _serve([2], password=pw)
    cli = RfbClient("127.0.0.1", port, vnc_password=pw)
    cli._set_pixel_format()
    t.join(timeout=5)
    assert cap["selected_sec"] == 2
    assert cap["des_ok"] is True
    assert cap["encodings"] == [0, -223]
