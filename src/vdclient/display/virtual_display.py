"""
Virtual monitors run via headless X servers. 
Point apps to virtual display = 50 app &. 
Needs xvfb and xauth on the path
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

from .. import logging_setup
from ..hardware import WM_CANDIDATES

log = logging_setup.get("vdisplay")

_FIRST_DISPLAY_NUMBER = 50
_STARTUP_POLLS = 50


@dataclass
class VirtualDisplay:
    display: str
    xauthority: str | None
    proc: subprocess.Popen
    wm_proc: subprocess.Popen | None


def _free_display_number() -> int:
    used = set()
    for s in glob.glob("/tmp/.X11-unix/X*"):
        n = s.rsplit("X", 1)[-1]
        if n.isdigit():
            used.add(int(n))
    n = _FIRST_DISPLAY_NUMBER
    while n in used:
        n += 1
    return n


def start(width: int, height: int) -> VirtualDisplay:
    """
    Launch a new xvfb server and return display/xauthority. 
    """
    if shutil.which("Xvfb") is None:
        raise RuntimeError(
            "Xvfb not found on PATH. Install it once on this machine: "
            "`sudo apt install xvfb` (Debian/Ubuntu/Mint), then retry."
        )
    if shutil.which("xauth") is None:
        raise RuntimeError(
            "xauth not found on PATH. Install it once on this machine: "
            "`sudo apt install xauth`, then retry."
        )

    num = _free_display_number()
    display = f":{num}"
    xauth_path = os.path.expanduser(f"~/.Xauthority-vd{num}")
    cookie = os.urandom(16).hex()
    subprocess.run(["xauth", "-f", xauth_path, "add", display, ".", cookie], check=True)

    proc = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", f"{width}x{height}x24",
         "-auth", xauth_path, "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    sock = f"/tmp/.X11-unix/X{num}"
    for _ in range(_STARTUP_POLLS):
        if os.path.exists(sock):
            break
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError(f"Xvfb did not start on {display} (no socket after 5s)")

    env = os.environ.copy()
    env["DISPLAY"] = display
    env["XAUTHORITY"] = xauth_path
    wm_proc = None
    for wm in WM_CANDIDATES:
        if shutil.which(wm):
            wm_proc = subprocess.Popen([wm], env=env, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            log.info("[video] virtual display %s: window manager '%s' started", display, wm)
            break
    else:
        log.info("[video] virtual display %s: no window manager found (tried %s) -- "
                 "apps will still render", display, ", ".join(WM_CANDIDATES))

    log.info("[video] virtual display %s up (%dx%d) -- point apps at it with: "
             "DISPLAY=%s XAUTHORITY=%s <app>", display, width, height, display, xauth_path)
    return VirtualDisplay(display=display, xauthority=xauth_path, proc=proc, wm_proc=wm_proc)


def stop(vd: VirtualDisplay) -> None:
    for p in (vd.wm_proc, vd.proc):
        if p is None:
            continue
        try:
            p.kill()
        except OSError:
            pass
    try:
        if vd.xauthority and os.path.exists(vd.xauthority):
            os.remove(vd.xauthority)
    except OSError:
        pass
