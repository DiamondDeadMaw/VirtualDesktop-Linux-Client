"""
Selects capture backend: x11grab, kmsgrab, or test pattern.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess

from .. import logging_setup
from ..display.xsession import running_x_display

log = logging_setup.get("video")

_PROBE_TIMEOUT = 15
_GETCAP_TIMEOUT = 10


def resolve_source(source: str) -> str:
    if source not in ("auto", "desktop"):
        return source
    if running_x_display():
        return "x11"
    if (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or os.environ.get("WAYLAND_DISPLAY")):
        return "kmsgrab"
    return "test"


def kms_card_device() -> str | None:
    # kmsgrab needs a modeset card node
    cards = sorted(glob.glob("/dev/dri/card*"))
    return cards[0] if cards else None


def kmsgrab_viable() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not glob.glob("/dev/dri/card*"):
        return False
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-h", "demuxer=kmsgrab"],
                           capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
        if "kmsgrab" not in (r.stdout + r.stderr):
            log.info("[video] kmsgrab not viable: this ffmpeg build lacks the "
                     "kmsgrab demuxer")
            return False
    except (OSError, subprocess.SubprocessError):
        return False

    if getattr(os, "geteuid", lambda: 1000)() == 0:
        return True

    getcap = shutil.which("getcap")
    if getcap:
        try:
            r = subprocess.run([getcap, os.path.realpath(ffmpeg)],
                               capture_output=True, text=True, timeout=_GETCAP_TIMEOUT)
            if "cap_sys_admin" in r.stdout.lower():
                return True
        except (OSError, subprocess.SubprocessError):
            pass

    log.info("[video] kmsgrab is available but ffmpeg lacks CAP_SYS_ADMIN -- fix once "
             "with: sudo setcap cap_sys_admin+ep $(command -v ffmpeg) -- falling back "
             "to x11grab+hwupload (higher CPU)")
    return False
