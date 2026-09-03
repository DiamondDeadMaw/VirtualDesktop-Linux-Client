"""
Streams encoded video, and discards inbound position updates from quest
"""
from __future__ import annotations

import threading

from .. import logging_setup
from . import audio

log = logging_setup.get("video")

_drain_inbound = audio.drain


def handle(stream, addr, port, session, config) -> None:
    if not config.video.enabled:
        log.info("[%d] %s: video disabled (video.enabled=false), holding channel open",
                 port, addr)
        audio.drain(stream.socket)
        return

    threading.Thread(target=_drain_inbound, args=(stream.socket,), daemon=True).start()

    from ..video.session import stream_video

    stream_video(stream, config, session=session)
