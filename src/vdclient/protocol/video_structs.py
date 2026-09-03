"""
Video channel binary layouts. Fields are little-endian.
"""
from __future__ import annotations

import struct
import time

PACKET_FRAME = 0
PACKET_SLICE = 1
PACKET_VIDEO_FORMAT = 2
PACKET_SUBTITLE = 3

SOURCE_DESKTOP = 0
SOURCE_VR = 1
SOURCE_VIDEO = 2
SOURCE_LOCAL_VIDEO = 3

CODEC_AUTOMATIC = 0
CODEC_H264 = 1
CODEC_HEVC = 2

VIDEO_FORMAT_SIZE = 88
FRAME_METADATA_SIZE = 160
SLICE_HEADER_SIZE = 16
FRAME_METADATA_DATASIZE_OFFSET = 152


def build_video_format(
    *,
    width: int,
    height: int,
    fps: int,
    codec: int = CODEC_H264,
    source: int = SOURCE_DESKTOP,
    bounds: list[tuple[int, int, int, int]] | None = None,
) -> bytes:
    if not bounds:
        bounds = [(0, 0, width, height)]
    stream_count = len(bounds)
    buf = bytearray(VIDEO_FORMAT_SIZE)
    struct.pack_into("<B", buf, 0, source)
    struct.pack_into("<i", buf, 4, codec)
    struct.pack_into("<i", buf, 8, fps)
    struct.pack_into("<ii", buf, 12, width, height)
    struct.pack_into("<i", buf, 20, stream_count)
    struct.pack_into("<iiii", buf, 24, *bounds[0])
    # quest configures all decoders with primary frame size, bounds set 3d placement
    if stream_count > 1:
        struct.pack_into("<ii", buf, 40, width, height)
        struct.pack_into("<iiii", buf, 48, *bounds[1])
    if stream_count > 2:
        struct.pack_into("<ii", buf, 64, width, height)
        struct.pack_into("<iiii", buf, 72, *bounds[2])
    return bytes(buf)


def build_frame_metadata(
    *,
    data_size: int,
    frame_index: int,
    is_sync: bool,
    width: int,
    height: int,
    timestamp_us: int | None = None,
) -> bytes:
    if timestamp_us is None:
        timestamp_us = int(time.monotonic() * 1_000_000)
    buf = bytearray(FRAME_METADATA_SIZE)
    struct.pack_into("<q", buf, 0, timestamp_us)
    struct.pack_into("<i", buf, 8, frame_index)
    struct.pack_into("<B", buf, 12, SOURCE_DESKTOP)
    struct.pack_into("<B", buf, 13, 1 if is_sync else 0)
    struct.pack_into("<B", buf, 14, 0)
    struct.pack_into("<ii", buf, 144, width, height)
    struct.pack_into("<i", buf, FRAME_METADATA_DATASIZE_OFFSET, data_size)
    return bytes(buf)


def build_slice_header(*, data_size: int, stream_index: int,
                       timestamp_us: int | None = None) -> bytes:
    if timestamp_us is None:
        timestamp_us = int(time.monotonic() * 1_000_000)
    buf = bytearray(SLICE_HEADER_SIZE)
    struct.pack_into("<q", buf, 0, timestamp_us)
    struct.pack_into("<i", buf, 8, stream_index)
    struct.pack_into("<i", buf, 12, data_size)
    return bytes(buf)


def frame_packet(metadata: bytes, au: bytes) -> bytes:
    return bytes([PACKET_FRAME]) + metadata + au


def slice_packet(header: bytes, au: bytes) -> bytes:
    return bytes([PACKET_SLICE]) + header + au


def video_format_packet(fmt: bytes) -> bytes:
    return bytes([PACKET_VIDEO_FORMAT]) + fmt
