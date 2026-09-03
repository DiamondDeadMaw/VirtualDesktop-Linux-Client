"""
Maps monitor config entries into capture plans.
"""
from __future__ import annotations

import os

from .. import logging_setup
from ..display import virtual_display, virtual_output

log = logging_setup.get("video")


class CapturePlanner:

    def __init__(self, *, width: int, height: int, base_source: str, cap_source: str,
                 base_display: str, base_env: dict,
                 primary_region: tuple[int, int, int, int] | None):
        self.width = width
        self.height = height
        self.base_source = base_source
        self.cap_source = cap_source
        self.base_display = base_display
        self.base_env = base_env
        self.primary_region = primary_region

    def plan(self, entry) -> dict:
        if isinstance(entry, dict) and entry.get("virtual"):
            return self._virtual_plan(entry)
        region = entry
        if region is None and self.primary_region:
            region = self.primary_region
        return {"region": region, "source": self.cap_source, "display": self.base_display,
                "env": self.base_env, "vd": None, "vout": None}

    def _virtual_plan(self, entry: dict) -> dict:
        spec = entry["virtual"] if isinstance(entry["virtual"], dict) else {}
        vw = spec.get("width", self.width)
        vh = spec.get("height", self.height)

        if self.base_source == "x11":
            try:
                vout = virtual_output.add(vw, vh, self.base_env)
                return {"region": vout.region, "source": self.cap_source,
                        "display": self.base_display, "env": self.base_env,
                        "vd": None, "vout": vout}
            except (RuntimeError, OSError) as exc:
                log.warning("[video] could not extend the primary desktop (%s); falling "
                            "back to an isolated Xvfb display for this monitor", exc)

        vd = virtual_display.start(vw, vh)
        venv = os.environ.copy()
        venv["DISPLAY"] = vd.display
        if vd.xauthority:
            venv["XAUTHORITY"] = vd.xauthority
        return {"region": None, "source": "x11", "display": vd.display,
                "env": venv, "vd": vd, "vout": None}


def teardown(plan: dict, base_env: dict) -> None:
    if plan.get("vd"):
        virtual_display.stop(plan["vd"])
    if plan.get("vout"):
        virtual_output.remove(plan["vout"], base_env)


def bounds_for(count: int, width: int, height: int) -> list[tuple[int, int, int, int]]:
    return [(i * width, 0, width, height) for i in range(count)]
