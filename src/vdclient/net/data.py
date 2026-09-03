"""
Data channel- responds to the quests bandwith check, then discards all headset and controller data
"""
from __future__ import annotations

from .. import logging_setup
from ..protocol.messages import write_int32

log = logging_setup.get("data")

# In packets
_INPUT_LOG_EVERY = 300


def handle(stream, addr, port, session, config) -> None:
    stream.write_frame(write_int32(0))
    log.info("[%d] sent bandwidth probe (Int32 0), now receiving FrameData input", port)

    count = 0
    while True:
        frame = stream.read_frame()
        count += 1
        if count == 1 or count % _INPUT_LOG_EVERY == 0:
            log.info("[%d] FrameData input: %d packets received (latest %d B)",
                     port, count, len(frame))
