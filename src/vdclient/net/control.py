"""
Control channel- sends virtual monitor control settings, and keep alive messages to the headset.
"""
from __future__ import annotations

import threading
import time

from .. import logging_setup
from ..protocol.constants import KEEPALIVE_INTERVAL, MAX_MONITORS
from ..protocol.messages import NetMessage, json_message

log = logging_setup.get("control")


def _keepalive(stream, session) -> None:
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        if session.control_stream is not stream:
            return
        try:
            with session.control_lock:
                stream.write_frame(
                    json_message("StreamerSettingsReload", dict(session.streamer_settings))
                )
        except OSError:
            return
        except ConnectionError:
            return


def handle(stream, addr, port, session, config) -> None:
    session.control_stream = stream

    if config.video.enabled:
        can_add = len(config.video.monitors) < MAX_MONITORS
        can_remove = len(config.video.monitors) > 1
        settings = {}
        if can_add:
            settings["CanAddMonitor"] = True
        if can_remove:
            settings["CanRemoveMonitor"] = True
        session.streamer_settings = settings
        session.write_control(json_message("StreamerSettingsReload", settings))
        log.info("[%d] sent StreamerSettingsReload (CanAddMonitor=%s, CanRemoveMonitor=%s)",
                 port, can_add, can_remove)

    threading.Thread(target=_keepalive, args=(stream, session), daemon=True).start()

    while True:
        msg = NetMessage.decode(stream.read_frame())
        log.info("[%d] recv %s (%d byte body)", port, msg.type_name, len(msg.body))
        if msg.type_name in ("AddMonitor", "RemoveMonitor"):
            queue = session.video_commands
            if queue is not None:
                queue.put(msg.type_name)
            else:
                log.info("[%d] %s requested but no active video session to send it to",
                         port, msg.type_name)
