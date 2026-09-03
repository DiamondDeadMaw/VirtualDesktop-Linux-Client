"""
Discovery: the golden capture regression plus a full round trip against the real
responder over a localhost UDP socket.
"""
from __future__ import annotations

import base64
import socket
import threading
import time
import uuid

import pytest
from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA1
from Crypto.PublicKey import RSA

from vdclient import config as config_module
from vdclient.net import discovery
from vdclient.net.registry import SessionRegistry
from vdclient.protocol.constants import DISCOVERY_IV, DISCOVERY_KEY
from vdclient.protocol.crypto import read_frames_from_blob, write_frames_to_blob

# An actual discovery request captured from a Quest 3 on 2026-07-03
# (docs/PROTOCOL.md section 9). This locks in the four-frame split: the earlier
# code read the plaintext as one frame and threw the rest away as padding, which
# is why the account always came back empty.
REAL_QUEST3_REQUEST = bytes.fromhex(
    "a6906530e4d712905ed9b3a42196931365c44df7f6877f1e6356555a8a7f1ff"
    "6a62748f25e5c9b529d7dabcb22b2f20204184ec1c90c83725e374098f80548"
    "7ea2cb74a408f1188fff208ef596a018268f60f2dd655a5c2d2d3bfb3f10609"
    "88e29fc9570c1e200459ae4f2f80bf9f0e6ce3f80f819607b583e914783f303"
    "3e143b54d5b26c6cd0facfc48ea8a0cefe484d218fac785b9d97876471f9364"
    "232a33e9ae1fa69784aa95a6b6907fb297e63492cabe0a2b889f7fd27d331c1"
    "0909b2ea9706f273c6505438a75e0bcf35a81a7b9faf2cb2a5bcd7fa9a29297"
    "b7c1cc66305614e692c5c84328c1694786b6d574780204c68599beb69e80aa5"
    "ee7315eb69bc48348e6d842dd2b97e987acfd612ad547af8cb435e1e22470c6"
    "9bdb783a858cd8e8a7ac49c52263db91e6f5aaddf"
)


def test_real_quest3_capture_splits_into_four_frames():
    frame1, frame2, frame3, frame4 = read_frames_from_blob(
        REAL_QUEST3_REQUEST, DISCOVERY_KEY, DISCOVERY_IV, 4
    )
    assert frame1.startswith(b"<RSAKeyValue>")
    assert frame1.endswith(b"</RSAKeyValue>")
    assert frame2 == b"\x00", "expected the 0x00 separator"
    assert frame3 == b"\x01", "expected platform=1 (Meta/Quest)"
    assert frame4 == b"DiamondDeadMaw"


def _b64_int(n: int, length: int) -> str:
    return base64.b64encode(n.to_bytes(length, "big")).decode("ascii")


@pytest.fixture
def responder():
    """The real discovery responder on a high port, so no privileges are needed."""
    registry = SessionRegistry()
    cfg = config_module.Config()
    connection_id = uuid.uuid4().bytes
    port = 48850
    thread = threading.Thread(
        target=discovery.run, args=(connection_id, registry, cfg),
        kwargs={"port": port}, daemon=True,
    )
    thread.start()
    time.sleep(0.3)
    return registry, cfg, port


def test_full_discovery_roundtrip(responder):
    registry, cfg, port = responder

    client_rsa = RSA.generate(1024)
    pub_xml = (
        "<RSAKeyValue>"
        f"<Modulus>{_b64_int(client_rsa.n, client_rsa.size_in_bytes())}</Modulus>"
        f"<Exponent>{_b64_int(client_rsa.e, 3)}</Exponent>"
        "</RSAKeyValue>"
    ).encode("ascii")

    request = write_frames_to_blob(
        [pub_xml, b"\x00", bytes([1]), b"test@example.com"],
        DISCOVERY_KEY, DISCOVERY_IV,
    )

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(5.0)
    try:
        client.sendto(request, ("127.0.0.1", port))
        reply, _ = client.recvfrom(65535)
    finally:
        client.close()

    # RSA-OAEP(sessionKey + IV) is 128 bytes, then the AES-wrapped Computer XML.
    rsa_blob, enc_computer = reply[:128], reply[128:]
    material = PKCS1_OAEP.new(client_rsa, hashAlgo=SHA1).decrypt(rsa_blob)
    session_key, session_iv = material[:32], material[32:]
    assert len(session_key) == 32 and len(session_iv) == 16

    (computer_xml,) = read_frames_from_blob(enc_computer, session_key, session_iv, 1)
    text = computer_xml.decode("utf-8")
    assert text.startswith("<Computer ")
    assert f"<Name>{cfg.identity.name}</Name>" in text
    assert f"<OS>{cfg.identity.os_name}</OS>" in text

    # The TCP channels reuse exactly this key.
    session = registry.get("127.0.0.1")
    assert session is not None
    assert session.keys == (session_key, session_iv)


def test_connect_nudge_is_not_answered(responder):
    """The 17-byte nudge means the user tapped Connect; it needs no reply, and
    answering it would be wrong."""
    _registry, _cfg, port = responder
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(0.75)
    try:
        client.sendto(uuid.uuid4().bytes + b"\x01", ("127.0.0.1", port))
        with pytest.raises(socket.timeout):
            client.recvfrom(65535)
    finally:
        client.close()


def test_short_packet_is_ignored(responder):
    _registry, _cfg, port = responder
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(0.75)
    try:
        client.sendto(b"\x00" * 100, ("127.0.0.1", port))
        with pytest.raises(socket.timeout):
            client.recvfrom(65535)
    finally:
        client.close()


def test_garbage_long_packet_does_not_kill_the_responder(responder):
    """A malformed long datagram must be logged and skipped, not end the loop --
    the next real request still has to be answered."""
    _registry, _cfg, port = responder
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(0.75)
    try:
        client.sendto(b"\xff" * 400, ("127.0.0.1", port))
        with pytest.raises(socket.timeout):
            client.recvfrom(65535)
    finally:
        client.close()

    # Still alive: a valid request is answered.
    client_rsa = RSA.generate(1024)
    pub_xml = (
        "<RSAKeyValue>"
        f"<Modulus>{_b64_int(client_rsa.n, client_rsa.size_in_bytes())}</Modulus>"
        f"<Exponent>{_b64_int(client_rsa.e, 3)}</Exponent>"
        "</RSAKeyValue>"
    ).encode("ascii")
    request = write_frames_to_blob([pub_xml, b"\x00", bytes([1]), b""],
                                   DISCOVERY_KEY, DISCOVERY_IV)
    client2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client2.settimeout(5.0)
    try:
        client2.sendto(request, ("127.0.0.1", port))
        reply, _ = client2.recvfrom(65535)
    finally:
        client2.close()
    assert len(reply) > 128
