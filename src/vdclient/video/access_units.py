"""
Splits bitstream into access units and manages frame drops.
"""
from __future__ import annotations

import queue

from .. import logging_setup

log = logging_setup.get("video")

AUD = b"\x00\x00\x00\x01\x09"
# 3-byte start code matches both 3- and 4-byte encoder output
SPS = b"\x00\x00\x01\x67"


def split_access_units(buf: bytes):
    units = []
    start = buf.find(AUD)
    if start == -1:
        return units, buf
    prefix = buf[:start] if start > 0 else b""
    while True:
        nxt = buf.find(AUD, start + len(AUD))
        if nxt == -1:
            break
        au = buf[start:nxt]
        if prefix:
            # vaapi puts sps/pps before aud, reattach after aud so quest decoder is happy
            au = au[:6] + prefix + au[6:]
            prefix = b""
        units.append(au)
        start = nxt
    return units, buf[start:]


class DropGate:
    """Drops frames after loss until the next keyframe to avoid corruption."""

    def __init__(self, index: int):
        self._index = index
        self._desynced = False
        self.dropped = 0
        self._reported = 0

    def offer(self, out_q, au: bytes, is_sync: bool) -> None:
        if self._desynced and not is_sync:
            self._note()
            return
        try:
            out_q.put_nowait((self._index, au, is_sync))
        except queue.Full:
            self._note()
            self._desynced = True
            return
        if self._desynced:
            self._desynced = False
            log.info("[video] monitor %d: resynced on keyframe after %d dropped "
                     "access unit(s)", self._index, self.dropped)

    def _note(self) -> None:
        self.dropped += 1
        if self.dropped == 1 or self.dropped >= self._reported * 4:
            self._reported = max(1, self.dropped)
            log.info("[video] monitor %d: dropped %d access unit(s) - send queue full "
                     "(sender thread is not draining it: CPU starvation or a blocked "
                     "socket); holding output until the next keyframe to avoid a "
                     "corrupt picture", self._index, self.dropped)
