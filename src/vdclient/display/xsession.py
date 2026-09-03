"""
Find the X session to capture
"""
from __future__ import annotations

import glob
import os
import re
import subprocess

from .. import logging_setup

log = logging_setup.get("video")

_XRANDR_TIMEOUT = 10


def running_x_display() -> str | None:
    """Return the actrive display, or guess lowest indexed one"""
    if os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    for s in sorted(glob.glob("/tmp/.X11-unix/X*")):
        n = s.rsplit("X", 1)[-1]
        if n.isdigit():
            return f":{n}"
    return None


def find_xauthority(display: str) -> str | None:
    """Find an X auth cookie so capture can connect from outside the session."""
    if os.environ.get("XAUTHORITY") and os.path.exists(os.environ["XAUTHORITY"]):
        return os.environ["XAUTHORITY"]
    home = os.path.expanduser("~/.Xauthority")
    if os.path.exists(home):
        return home
    for cmdline in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(cmdline, "rb") as f:
                parts = f.read().split(b"\x00")
        except OSError:
            continue
        if not parts or not (parts[0].endswith(b"/X") or parts[0].endswith(b"Xorg")
                             or parts[0] in (b"X", b"Xorg")):
            continue
        for i, p in enumerate(parts):
            if p == b"-auth" and i + 1 < len(parts):
                cand = parts[i + 1].decode("utf-8", "replace")
                if os.path.exists(cand):
                    return cand
    for pat in ("/var/run/lightdm/root/" + display, "/run/lightdm/root/" + display,
                "/var/run/sddm/*", "/run/user/*/gdm/Xauthority"):
        for cand in glob.glob(pat):
            if os.path.exists(cand):
                return cand
    return None


def primary_geometry(env) -> tuple[int, int, int, int] | None:
    try:
        r = subprocess.run(["xrandr", "--query"], env=env, capture_output=True,
                           text=True, timeout=_XRANDR_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None  # xrandr missing or no X- fall back to the full root window
    line = next((ln for ln in r.stdout.splitlines() if " connected primary " in ln), None)
    if line is None:  # no output flagged primary use the first connected one
        line = next((ln for ln in r.stdout.splitlines() if " connected" in ln), None)
    if line is None:
        return None
    m = re.search(r"\b(\d+)x(\d+)\+(\d+)\+(\d+)", line)
    if not m:
        return None
    w, h, x, y = (int(g) for g in m.groups())
    return (x, y, w, h)


def base_environment(source: str) -> tuple[dict, str]:
    env = os.environ.copy()
    display = os.environ.get("DISPLAY") or ":0"
    if source == "x11":
        display = running_x_display() or ":0"
        env["DISPLAY"] = display
        xauth = find_xauthority(display)
        if xauth:
            env["XAUTHORITY"] = xauth
        log.info("[video] X11 capture on DISPLAY=%s XAUTHORITY=%s",
                 display, env.get("XAUTHORITY", "(default ~/.Xauthority)"))
    return env, display
