"""Framing and chained-AES primitives."""
from __future__ import annotations

import struct

import pytest

from vdclient.protocol.crypto import (
    decode_varint,
    encode_varint,
    read_frames_from_blob,
    round_up_16,
    write_frames_to_blob,
)
from vdclient.protocol.messages import write_int32


@pytest.mark.parametrize("n", [0, 1, 5, 127, 128, 129, 300, 16383, 16384, 262143, 1 << 21])
def test_varint_roundtrip(n):
    enc = encode_varint(n)
    value, consumed = decode_varint(enc, 0)
    assert value == n
    assert consumed == len(enc)


@pytest.mark.parametrize("n,expected", [(0, 0), (1, 16), (15, 16), (16, 16), (17, 32), (160, 160)])
def test_round_up_16(n, expected):
    assert round_up_16(n) == expected


def test_multi_frame_blob_roundtrip():
    key, iv = b"\x55" * 32, b"\x66" * 16
    blob = write_frames_to_blob([b"frame-one-payload", b"frame-two"], key, iv)
    f1, f2 = read_frames_from_blob(blob, key, iv, 2)
    assert f1 == b"frame-one-payload"
    assert f2 == b"frame-two"


def test_frame_sizes_span_block_boundaries():
    key, iv = bytes(range(32)), bytes(range(16))
    payloads = [b"", b"a", b"x" * 15, b"y" * 16, b"z" * 17, bytes(range(256)) * 3]
    blob = write_frames_to_blob(payloads, key, iv)
    assert read_frames_from_blob(blob, key, iv, len(payloads)) == payloads


def test_bandwidth_probe_is_big_endian_int32_zero():
    """The data channel must send Int32(0) so the Quest's TryMeasureBitrate ->
    NetworkBinaryReader.ReadInt32() (big-endian) reads 0 and returns false,
    unblocking MeasureBandwidth."""
    key, iv = b"\x33" * 32, b"\x44" * 16
    blob = write_frames_to_blob([write_int32(0)], key, iv)
    (frame,) = read_frames_from_blob(blob, key, iv, 1)
    assert frame == b"\x00\x00\x00\x00"
    (num,) = struct.unpack(">i", frame)
    assert num == 0


def test_truncated_blob_raises():
    key, iv = b"\x11" * 32, b"\x22" * 16
    blob = write_frames_to_blob([b"payload"], key, iv)
    with pytest.raises(ConnectionError):
        read_frames_from_blob(blob[:8], key, iv, 1)
