"""
Use forced drm outputs to create virtual monitors. 
Fake a connection to register as a real monitor, so needs sudo. 
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from .. import logging_setup
from ..hardware import PANEL_CONNECTOR, VIRTUAL_OUTPUT_POOL

log = logging_setup.get("vout")

_LOCK = threading.Lock()
_in_use: set[str] = set()
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_RUN_TIMEOUT = 30


class VirtualOutput:
    def __init__(self, connector: str, xr_name: str, region: tuple[int, int, int, int]):
        # DRM name
        self.connector = connector
        # xrandr name
        self.xr_name = xr_name
        # The quad to capture on primary display
        self.region = region


def _xr_name(conn: str) -> str:
    return conn.replace("HDMI-A-", "HDMI-")


def _run(cmd: list[str], env, timeout: int = _RUN_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("[vout] %s could not run: %r", " ".join(cmd), exc)
        raise


def _connected_right_edge(env) -> int:
    r = _run(["xrandr", "--query"], env)
    edge = 0
    for line in r.stdout.splitlines():
        if " connected" not in line:
            continue
        m = re.search(r"\b(\d+)x(\d+)\+(\d+)\+(\d+)", line)
        if m:
            edge = max(edge, int(m.group(1)) + int(m.group(3)))
    return edge


def _geometry(xr_name: str, env) -> tuple[int, int, int, int] | None:
    r = _run(["xrandr", "--query"], env)
    for line in r.stdout.splitlines():
        if line.startswith(xr_name + " "):
            m = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
            if m:
                w, h, x, y = (int(g) for g in m.groups())
                return (x, y, w, h)
    return None


def add(width: int, height: int, env) -> VirtualOutput:
    """ Returns VirtualOutput whose .region is the capture rect."""
    with _LOCK:
        conn = next((c for c in VIRTUAL_OUTPUT_POOL if c not in _in_use), None)
        if conn is None:
            raise RuntimeError("no free DRM connector left to force a virtual monitor onto")
        xr = _xr_name(conn)
        x = _connected_right_edge(env)

        enable = os.fspath(_SCRIPTS / "enable_virtual_monitor.sh")
        r = _run(["sudo", "-n", enable, conn], env)
        if r.returncode != 0:
            raise RuntimeError(
                f"enable_virtual_monitor.sh {conn} failed - passwordless sudo not set "
                f"up? run scripts/setup.sh. ({(r.stderr or r.stdout).strip()[:200]})")

        _run(["xrandr", "--output", xr, "--auto", "--pos", f"{x}+0"], env)
        region = _geometry(xr, env)
        if region is None:
            _run(["sudo", "-n", os.fspath(_SCRIPTS / "disable_virtual_monitor.sh"), conn], env)
            raise RuntimeError(f"{xr} never appeared connected in xrandr after forcing it on")

        _in_use.add(conn)
        log.info("[vout] virtual monitor live on %s at +%d+%d (%dx%d). Drag windows off "
                 "the right edge of your physical screen (%s) to use it.",
                 xr, region[0], region[1], region[2], region[3], PANEL_CONNECTOR)
        return VirtualOutput(conn, xr, region)


def remove(vout: VirtualOutput, env) -> None:
    with _LOCK:
        disable = os.fspath(_SCRIPTS / "disable_virtual_monitor.sh")
        _run(["sudo", "-n", disable, vout.connector], env)
        _in_use.discard(vout.connector)
        log.info("[vout] removed virtual monitor %s", vout.xr_name)
