"""
Resolves encode resolution, aligned down to 16px macroblocks.
"""
from __future__ import annotations

from .. import logging_setup
from ..hardware import MACROBLOCK

log = logging_setup.get("video")

# 1072 is nearest 16px macroblock multiple below 1080
FALLBACK_SIZE = (1920, 1072)


def align_down(value: int, multiple: int = MACROBLOCK) -> int:
    return max(multiple, (value // multiple) * multiple)


def resolve(config_width: int | None, config_height: int | None,
            primary_region: tuple[int, int, int, int] | None) -> tuple[int, int]:
    if config_width is not None and config_height is not None:
        log.info("[video] encode size %dx%d (from config)", config_width, config_height)
        return config_width, config_height

    if primary_region is not None:
        _, _, w, h = primary_region
        aligned = (align_down(w), align_down(h))
        if aligned != (w, h):
            log.info("[video] encode size %dx%d (primary output is %dx%d, aligned down "
                     "to a multiple of %d to avoid a decoder crop artefact)",
                     aligned[0], aligned[1], w, h, MACROBLOCK)
        else:
            log.info("[video] encode size %dx%d (primary output)", aligned[0], aligned[1])
        return aligned

    width, height = FALLBACK_SIZE
    log.info("[video] encode size %dx%d (no primary output detected, using the "
             "fallback size)", width, height)
    return width, height
