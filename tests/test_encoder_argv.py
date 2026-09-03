"""
Snapshot tests for the encoder command lines.

These argv lists are captured verbatim from the prototype that was verified
working on a real Quest 3. This is the highest-drift-risk code in the project and
the cheapest to lock down: a single wrong flag here is a black headset with no
error message anywhere.
"""
from __future__ import annotations

import pytest

from vdclient.video.encoders import ffmpeg_cmd, input_args, vd_encoder_cmd

DEV = "/dev/dri/renderD128"
KMS = "/dev/dri/card0"


def cmd(source, region, encoder, index=0):
    return ffmpeg_cmd(source, region, index, 1600, 896, 60, encoder, ":0", DEV,
                      kms_device=KMS)


def test_x11_vaapi_fullscreen():
    assert cmd("x11", None, "h264_vaapi") == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-f", "x11grab", "-draw_mouse", "1", "-framerate", "60", "-i", ":0",
        "-vaapi_device", DEV,
        "-vf", "hwupload,scale_vaapi=w=1600:h=896:format=nv12",
        "-c:v", "h264_vaapi", "-profile:v", "main",
        "-aud", "1", "-g", "60", "-bf", "0",
        "-f", "h264", "-",
    ]


def test_x11_vaapi_region():
    assert cmd("x11", (0, 0, 1600, 896), "h264_vaapi") == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-f", "x11grab", "-draw_mouse", "1", "-framerate", "60",
        "-video_size", "1600x896", "-i", ":0+0,0",
        "-vaapi_device", DEV,
        "-vf", "hwupload,scale_vaapi=w=1600:h=896:format=nv12",
        "-c:v", "h264_vaapi", "-profile:v", "main",
        "-aud", "1", "-g", "60", "-bf", "0",
        "-f", "h264", "-",
    ]


def test_kmsgrab_vaapi_with_crop():
    """The zero-copy path: hwmap derives the VA device from the kms one, so
    -vaapi_device must NOT be passed, and the crop selects this monitor's
    rectangle off the shared scanout."""
    assert cmd("kmsgrab", (100, 0, 1280, 720), "h264_vaapi") == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-device", KMS, "-f", "kmsgrab", "-framerate", "60", "-i", "-",
        "-vf", "hwmap=derive_device=vaapi,crop=1280:720:100:0,"
               "scale_vaapi=w=1600:h=896:format=nv12",
        "-c:v", "h264_vaapi", "-profile:v", "main",
        "-aud", "1", "-g", "60", "-bf", "0",
        "-f", "h264", "-",
    ]


def test_kmsgrab_vaapi_whole_scanout():
    assert cmd("kmsgrab", None, "h264_vaapi") == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-device", KMS, "-f", "kmsgrab", "-framerate", "60", "-i", "-",
        "-vf", "hwmap=derive_device=vaapi,scale_vaapi=w=1600:h=896:format=nv12",
        "-c:v", "h264_vaapi", "-profile:v", "main",
        "-aud", "1", "-g", "60", "-bf", "0",
        "-f", "h264", "-",
    ]


def test_x11_software():
    assert cmd("x11", None, "libx264") == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-f", "x11grab", "-draw_mouse", "1", "-framerate", "60", "-i", ":0",
        "-vf", "scale=1600:896,format=yuv420p",
        "-c:v", "libx264", "-profile:v", "baseline",
        "-preset", "ultrafast", "-tune", "zerolatency",
        "-x264-params", "aud=1:repeat-headers=1:keyint=60:min-keyint=60:scenecut=0",
        "-f", "h264", "-",
    ]


def test_test_pattern_software():
    assert cmd("test", None, "libx264") == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-re", "-f", "lavfi", "-i", "testsrc2=size=1600x896:rate=60",
        "-vf", "scale=1600:896,format=yuv420p",
        "-c:v", "libx264", "-profile:v", "baseline",
        "-preset", "ultrafast", "-tune", "zerolatency",
        "-x264-params", "aud=1:repeat-headers=1:keyint=60:min-keyint=60:scenecut=0",
        "-f", "h264", "-",
    ]


@pytest.mark.parametrize("source,encoder", [
    ("x11", "h264_vaapi"), ("kmsgrab", "h264_vaapi"), ("test", "h264_vaapi"),
])
def test_aud_is_always_requested_for_vaapi(source, encoder):
    """h264_vaapi does not insert Access Unit Delimiters by default, and the
    splitter keys on them to find frame boundaries. Without -aud 1 no access unit
    is ever emitted and the headset stays black."""
    argv = cmd(source, None, encoder)
    assert "-aud" in argv and argv[argv.index("-aud") + 1] == "1"


def test_software_path_requests_aud_via_x264_params():
    argv = cmd("x11", None, "libx264")
    assert "aud=1" in argv[argv.index("-x264-params") + 1]


def test_vaapi_never_uses_constrained_baseline():
    """Broadwell/HD 5500 exposes Main and High but no constrained_baseline
    entrypoint: asking for baseline fails with "No usable encoding entrypoint"."""
    argv = cmd("x11", None, "h264_vaapi")
    assert argv[argv.index("-profile:v") + 1] == "main"


def test_zerocopy_does_not_pass_vaapi_device():
    """Initialising a second VA device conflicts with hwmap's derive_device."""
    assert "-vaapi_device" not in cmd("kmsgrab", None, "h264_vaapi")


def test_test_patterns_differ_per_monitor():
    first = cmd("test", None, "libx264", index=0)
    second = cmd("test", None, "libx264", index=1)
    third = cmd("test", None, "libx264", index=2)
    patterns = {argv[argv.index("-i") + 1] for argv in (first, second, third)}
    assert len(patterns) == 3


def test_crtc_id_is_passed_when_set():
    argv = input_args("kmsgrab", None, 0, 1600, 896, 60, ":0",
                      kms_device=KMS, crtc_id=42)
    assert argv == ["-device", KMS, "-f", "kmsgrab", "-framerate", "60",
                    "-crtc_id", "42", "-i", "-"]


def test_vd_encoder_argv():
    plan = {"display": ":0", "region": (100, 50, 1280, 720),
            "env": {"XAUTHORITY": "/home/u/.Xauthority"}, "crtc_id": None}
    assert vd_encoder_cmd("/opt/vd_encoder", plan, 1600, 896, 60, DEV, KMS) == [
        "/opt/vd_encoder",
        "--kms-device", KMS,
        "--vaapi-device", DEV,
        "--size", "1600x896",
        "--fps", "60",
        "--display", ":0",
        # ffmpeg's own W:H:X:Y order, so both paths describe the rect identically
        "--crop", "1280:720:100:50",
        "--xauthority", "/home/u/.Xauthority",
    ]


def test_vd_encoder_argv_no_cursor_and_no_region():
    plan = {"display": ":0", "region": None, "env": {}, "crtc_id": 7}
    assert vd_encoder_cmd("/opt/vd_encoder", plan, 1600, 896, 60, DEV, KMS,
                          cursor=False) == [
        "/opt/vd_encoder",
        "--kms-device", KMS,
        "--vaapi-device", DEV,
        "--size", "1600x896",
        "--fps", "60",
        "--display", ":0",
        "--crtc-id", "7",
        "--no-cursor",
    ]
