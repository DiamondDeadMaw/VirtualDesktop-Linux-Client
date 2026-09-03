"""
No audio yet. But quest client wants this open. 
"""
from __future__ import annotations

import socket
import time

_DRAIN_CHUNK = 4096


def hold_open(idle_seconds: float) -> None:
    while True:
        time.sleep(idle_seconds)


def drain(sock) -> None:
    """Discard anytrhing we receive"""
    while True:
        try:
            if not sock.recv(_DRAIN_CHUNK):
                return 
        except socket.timeout:
            continue
        except OSError:
            return


def handle(stream, addr, port, session, config) -> None:
    drain(stream.socket)
