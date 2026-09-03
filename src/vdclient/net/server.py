"""
TCP listeners for the 4 quest channels. All share the same sesh key
"""
from __future__ import annotations

import socket
import threading

from .. import logging_setup
from ..protocol.constants import (
    AUDIO_PORT,
    CHANNEL_NAMES,
    CONTROL_PORT,
    DATA_PORT,
    VIDEO_PORT,
)
from ..protocol.crypto import ChainCipherStream
from . import audio, control, data, video_channel

log = logging_setup.get("net")

_HANDLERS = {
    CONTROL_PORT: control.handle,
    DATA_PORT: data.handle,
    VIDEO_PORT: video_channel.handle,
    AUDIO_PORT: audio.handle,
}


def _handle_connection(sock: socket.socket, addr, port: int, registry, config,
                       role: int) -> None:
    peer_ip = addr[0]
    session = registry.get(peer_ip)
    keys = session.keys if session is not None else None
    if keys is None:
        log.info("[%d] %s: no negotiated session key for this peer yet, closing", port, addr)
        sock.close()
        return
    key, iv = keys

    if role == VIDEO_PORT:
        # If the quest stops reading our stream, it will hang the thread on the pc
        sock.settimeout(config.tunables.video_send_timeout)

    stream = ChainCipherStream(sock, key, iv)
    channel = CHANNEL_NAMES.get(role, "unknown")
    log.info("[%d] connected (%s channel)", port, channel)

    handler = _HANDLERS.get(role, audio.handle)
    try:
        handler(stream, addr, port, session, config)
    except ConnectionError:
        log.info("[%d] connection closed (%s)", port, channel)
    except socket.timeout:
        log.info("[%d] %s channel timed out (Quest stopped reading -- likely can't "
                 "keep up), closing so it can reconnect cleanly", port, channel)
    except Exception as exc:
        log.warning("[%d] stream error (%s): %r", port, channel, exc)
    finally:
        sock.close()


def run(port: int, registry, config, role: int | None = None) -> None:
    role = port if role is None else role
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", port))
    listener.listen(5)
    log.info("[%d] listening (tcp)", port)

    while True:
        client_sock, addr = listener.accept()
        threading.Thread(
            target=_handle_connection,
            args=(client_sock, addr, port, registry, config, role),
            daemon=True,
        ).start()
