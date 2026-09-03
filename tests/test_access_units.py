"""Access-unit splitting and the drop-until-keyframe gate."""
from __future__ import annotations

import queue

from vdclient.video.access_units import AUD, SPS, DropGate, split_access_units

_AUD = AUD + b"\x10"          # AUD NAL plus its one payload byte
_SPS = SPS + b"\xAB"


def test_no_aud_yields_nothing_and_buffers_everything():
    units, rest = split_access_units(b"no start codes here")
    assert units == []
    assert rest == b"no start codes here"


def test_incomplete_trailing_unit_is_held_back():
    """Only complete AUs are returned; the tail waits for the next AUD."""
    units, rest = split_access_units(_AUD + b"slice1")
    assert units == []
    assert rest == _AUD + b"slice1"


def test_two_complete_units():
    buf = _AUD + b"one" + _AUD + b"two"
    units, rest = split_access_units(buf)
    assert units == [_AUD + b"one"]
    assert rest == _AUD + b"two"


def test_three_units():
    buf = _AUD + b"a" + _AUD + b"b" + _AUD + b"c"
    units, rest = split_access_units(buf)
    assert units == [_AUD + b"a", _AUD + b"b"]
    assert rest == _AUD + b"c"


def test_parameter_sets_before_the_first_aud_are_reattached_after_it():
    """h264_vaapi emits SPS/PPS BEFORE the AUD on keyframes, but Annex B requires
    the AUD to lead the access unit. The prefix must be moved after it, not
    dropped -- without the SPS the decoder has nothing to configure from."""
    buf = _SPS + _AUD + b"slice" + _AUD + b"next"
    units, rest = split_access_units(buf)
    assert len(units) == 1
    au = units[0]
    assert au.startswith(_AUD)          # AUD still first
    assert _SPS in au                   # parameter sets carried along
    assert au == _AUD + _SPS + b"slice"
    assert rest == _AUD + b"next"


def test_prefix_is_only_attached_once():
    buf = _SPS + _AUD + b"one" + _AUD + b"two" + _AUD + b"three"
    units, _ = split_access_units(buf)
    assert _SPS in units[0]
    assert _SPS not in units[1]


def test_sync_detection_matches_the_sps_start_code():
    """The 3-byte start code form matches both 3- and 4-byte encoder output."""
    assert SPS in b"\x00\x00\x00\x01\x67\xAB"
    assert SPS in b"\x00\x00\x01\x67\xAB"


def test_drop_gate_passes_through_when_there_is_room():
    q = queue.Queue(maxsize=4)
    gate = DropGate(0)
    gate.offer(q, b"au1", False)
    gate.offer(q, b"au2", True)
    assert q.get() == (0, b"au1", False)
    assert q.get() == (0, b"au2", True)
    assert gate.dropped == 0


def test_drop_gate_suppresses_deltas_until_the_next_keyframe():
    """A lost AU makes every following P-frame reference frames the decoder never
    got. Sending them anyway produces a stuck black band for the rest of the GOP,
    so everything is held until a self-contained sync frame arrives."""
    q = queue.Queue(maxsize=1)
    gate = DropGate(0)

    gate.offer(q, b"fills-the-queue", False)
    gate.offer(q, b"this-one-drops", False)      # queue full -> dropped, desynced
    assert gate.dropped == 1

    q.get()                                       # room again...
    gate.offer(q, b"still-a-delta", False)        # ...but still desynced
    assert gate.dropped == 2
    assert q.empty()

    gate.offer(q, b"keyframe", True)              # sync frame resynchronises
    assert q.get() == (0, b"keyframe", True)
    assert gate.dropped == 2


def test_drop_gate_carries_its_stream_index():
    q = queue.Queue(maxsize=2)
    DropGate(2).offer(q, b"au", True)
    assert q.get()[0] == 2
