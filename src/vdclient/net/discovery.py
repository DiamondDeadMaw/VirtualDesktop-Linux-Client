"""
UDP- discovery and pairing. Handles discovery, session key negotation, and the Comptuer object. 
"""
from __future__ import annotations

import base64
import socket
import xml.etree.ElementTree as ET

from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA1
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes

from .. import logging_setup
from ..protocol.computer import build_computer_xml
from ..protocol.constants import (
    CONNECT_NUDGE_LEN,
    DISCOVERY_IV,
    DISCOVERY_KEY,
    DISCOVERY_PORT,
    DISCOVERY_REQUEST_FRAMES,
    MIN_PAIRING_REQUEST_LEN,
    PLATFORM_NAMES,
)
from ..protocol.crypto import read_frames_from_blob, write_frames_to_blob

log = logging_setup.get("discovery")


def _rsa_from_dotnet_xml(xml_str: str) -> RSA.RsaKey:
    root = ET.fromstring(xml_str)
    modulus = base64.b64decode(root.findtext("Modulus"))
    exponent = base64.b64decode(root.findtext("Exponent"))
    n = int.from_bytes(modulus, "big")
    e = int.from_bytes(exponent, "big")
    return RSA.construct((n, e))


def _local_ip_towards(peer_ip: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_ip, DISCOVERY_PORT))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


def run(connection_id: bytes, registry, config, port: int = DISCOVERY_PORT) -> None:
    """registry is the SessionRegistry that the tcp listeners will look at later"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    log.info("[discovery] listening on udp/%d", port)

    while True:
        data, addr = sock.recvfrom(65535)

        if len(data) == CONNECT_NUDGE_LEN:
            log.info("[discovery] %s: CONNECT NUDGE (user tapped Connect); "
                     "TCP on 38810 imminent", addr)
            continue

        if len(data) <= MIN_PAIRING_REQUEST_LEN:
            log.info("[discovery] %s: ignoring unrecognized short packet (%d bytes)",
                     addr, len(data))
            continue

        try:
            rsa_pub_xml_bytes, _sep, platform_frame, account_frame = read_frames_from_blob(
                data, DISCOVERY_KEY, DISCOVERY_IV, DISCOVERY_REQUEST_FRAMES
            )
        except (ConnectionError, IndexError, ValueError) as exc:
            log.warning("[discovery] %s: failed to read the four expected discovery "
                        "frames (RSA key / 0x00 / platform / account): %r", addr, exc)
            continue

        try:
            rsa_pub_xml = rsa_pub_xml_bytes.decode("ascii")
            platform = platform_frame[0] if platform_frame else 0
            account = account_frame.decode("utf-8")
        except (UnicodeDecodeError, IndexError) as exc:
            log.warning("[discovery] %s: field parsing failed: %r", addr, exc)
            continue

        log.info("[discovery] %s: pairing request from platform=%d (%s) account=%r",
                 addr, platform, PLATFORM_NAMES.get(platform, "?"), account)

        try:
            rsa_key = _rsa_from_dotnet_xml(rsa_pub_xml)
        except (ET.ParseError, ValueError, TypeError) as exc:
            log.warning("[discovery] %s: failed to parse client RSA key: %r. "
                        "raw rsa_pub_xml=%r", addr, exc, rsa_pub_xml)
            continue

        session_key = get_random_bytes(32)
        session_iv = get_random_bytes(16)

        cipher_rsa = PKCS1_OAEP.new(rsa_key, hashAlgo=SHA1)
        rsa_blob = cipher_rsa.encrypt(session_key + session_iv)

        identity = config.identity
        local_ip = _local_ip_towards(addr[0])
        computer_xml = build_computer_xml(
            connection_id=connection_id,
            key=session_key,
            iv=session_iv,
            name=identity.name,
            description=identity.description,
            streamer_version=identity.streamer_version,
            allow_remote_connections=identity.allow_remote_connections,
            encrypt_local_traffic=identity.encrypt_local_traffic,
            encrypt_remote_traffic=identity.encrypt_remote_traffic,
            os_name=identity.os_name,
            region=identity.region,
            adapters=[(local_ip, identity.adapter_mac,
                       identity.adapter_wireless, identity.adapter_gigabit)],
        ).encode("utf-8")
        enc_computer = write_frames_to_blob([computer_xml], session_key, session_iv)

        sock.sendto(rsa_blob + enc_computer, addr)
        registry.set_keys(addr[0], session_key, session_iv)
        log.info("[discovery] %s: replied, session key negotiated (key=%s... iv=%s...)",
                 addr, session_key.hex()[:8], session_iv.hex()[:8])
