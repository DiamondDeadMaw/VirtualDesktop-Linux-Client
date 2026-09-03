"""
Byte layouts for the video channel. Every offset here was read off the real
assemblies with Marshal.OffsetOf; the Quest reads these as raw memory, so a
one-byte drift is a black screen with no error.
"""
from __future__ import annotations

import struct

import pytest

from vdclient.protocol.video_structs import (
    CODEC_H264,
    FRAME_METADATA_DATASIZE_OFFSET,
    FRAME_METADATA_SIZE,
    PACKET_FRAME,
    PACKET_SLICE,
    PACKET_VIDEO_FORMAT,
    SLICE_HEADER_SIZE,
    SOURCE_DESKTOP,
    VIDEO_FORMAT_SIZE,
    build_frame_metadata,
    build_slice_header,
    build_video_format,
    frame_packet,
    slice_packet,
    video_format_packet,
)


def test_struct_sizes():
    assert VIDEO_FORMAT_SIZE == 88
    assert FRAME_METADATA_SIZE == 160
    assert SLICE_HEADER_SIZE == 16
    assert FRAME_METADATA_DATASIZE_OFFSET == 152


def test_video_format_field_offsets():
    fmt = build_video_format(width=1920, height=1080, fps=72, codec=CODEC_H264)
    assert len(fmt) == VIDEO_FORMAT_SIZE
    assert fmt[0] == SOURCE_DESKTOP
    assert struct.unpack_from("<i", fmt, 4)[0] == CODEC_H264
    assert struct.unpack_from("<i", fmt, 8)[0] == 72
    assert struct.unpack_from("<ii", fmt, 12) == (1920, 1080)   # FrameSize
    assert struct.unpack_from("<i", fmt, 20)[0] == 1            # StreamCount
    assert struct.unpack_from("<iiii", fmt, 24) == (0, 0, 1920, 1080)  # Bounds


def test_video_format_is_little_endian():
    """The video channel is native LE -- the opposite of the big-endian control
    channel. Reading this as BE would give a nonsense codec and frame rate."""
    fmt = build_video_format(width=16, height=16, fps=1, codec=CODEC_H264)
    assert fmt[4:8] == b"\x01\x00\x00\x00"


def test_video_format_multi_monitor():
    fmt = build_video_format(width=1920, height=1080, fps=60,
                             bounds=[(0, 0, 1920, 1080), (1920, 0, 1920, 1080)])
    assert struct.unpack_from("<i", fmt, 20)[0] == 2
    assert struct.unpack_from("<iiii", fmt, 24) == (0, 0, 1920, 1080)
    assert struct.unpack_from("<ii", fmt, 40) == (1920, 1080)          # SecondaryFrameSize
    assert struct.unpack_from("<iiii", fmt, 48) == (1920, 0, 1920, 1080)


def test_video_format_three_monitors():
    b = [(0, 0, 800, 600), (800, 0, 800, 600), (1600, 0, 800, 600)]
    fmt = build_video_format(width=800, height=600, fps=60, bounds=b)
    assert struct.unpack_from("<i", fmt, 20)[0] == 3
    assert struct.unpack_from("<ii", fmt, 64) == (800, 600)            # TertiaryFrameSize
    assert struct.unpack_from("<iiii", fmt, 72) == (1600, 0, 800, 600)


def test_secondary_frame_size_always_equals_primary():
    """VideoPlayer.ConfigureCodec configures every decoder at the primary
    FrameSize, so all monitors must encode at the same size."""
    fmt = build_video_format(width=1600, height=896, fps=60,
                             bounds=[(0, 0, 1600, 896), (1600, 0, 640, 480)])
    assert struct.unpack_from("<ii", fmt, 12) == struct.unpack_from("<ii", fmt, 40)


def test_frame_metadata_field_offsets():
    meta = build_frame_metadata(data_size=4242, frame_index=7, is_sync=True,
                                width=1920, height=1080, timestamp_us=123456789)
    assert len(meta) == FRAME_METADATA_SIZE
    assert struct.unpack_from("<q", meta, 0)[0] == 123456789
    assert struct.unpack_from("<i", meta, 8)[0] == 7
    assert meta[12] == SOURCE_DESKTOP
    assert meta[13] == 1          # IsSyncFrame
    assert meta[14] == 0          # IsHDR
    assert struct.unpack_from("<ii", meta, 144) == (1920, 1080)
    assert struct.unpack_from("<i", meta, FRAME_METADATA_DATASIZE_OFFSET)[0] == 4242


def test_frame_metadata_pose_fields_are_zeroed():
    meta = build_frame_metadata(data_size=1, frame_index=0, is_sync=False,
                                width=16, height=16, timestamp_us=0)
    assert meta[16:144] == bytes(128)


@pytest.mark.parametrize("is_sync", [True, False])
def test_frame_metadata_sync_flag(is_sync):
    meta = build_frame_metadata(data_size=1, frame_index=0, is_sync=is_sync,
                                width=16, height=16, timestamp_us=0)
    assert meta[13] == (1 if is_sync else 0)


@pytest.mark.parametrize("stream_index", [1, 2])
def test_slice_header(stream_index):
    hdr = build_slice_header(data_size=77, stream_index=stream_index, timestamp_us=555)
    assert len(hdr) == SLICE_HEADER_SIZE
    assert struct.unpack_from("<q", hdr, 0)[0] == 555
    assert struct.unpack_from("<i", hdr, 8)[0] == stream_index
    assert struct.unpack_from("<i", hdr, 12)[0] == 77


def test_packet_prefixes():
    au = b"\xaa" * 40
    assert frame_packet(b"m" * 160, au)[0] == PACKET_FRAME
    assert frame_packet(b"m" * 160, au)[1:] == b"m" * 160 + au
    assert slice_packet(b"s" * 16, au)[0] == PACKET_SLICE
    assert video_format_packet(b"f" * 88)[0] == PACKET_VIDEO_FORMAT
