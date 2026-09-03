from __future__ import annotations

import logging
import sys


def configure(level: str = "INFO", timestamps: bool = True) -> None:
    fmt = "%(asctime)s %(message)s" if timestamps else "%(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))

    root = logging.getLogger("vdclient")
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False
    try:
        root.setLevel(level.upper())
    except ValueError:
        root.setLevel(logging.INFO)
        root.warning("unknown log level %r, using INFO", level)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"vdclient.{name}")
