"""
Entry point: runs the UDP discovery responder so a Quest on the same LAN can find
and pair with this box, plus TCP acceptors on the four session ports.

    python -m vdclient [--config config/vdclient.json]
"""
from __future__ import annotations

import argparse
import sys
import threading
import uuid

from . import config as config_module
from . import logging_setup
from .net import discovery, server
from .net.registry import SessionRegistry
from .protocol.constants import TCP_PORTS


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vdclient", description=__doc__)
    parser.add_argument("--config", metavar="PATH",
                        help="JSON config file (default: the first of "
                             "config/vdclient.json, ~/.config/vdclient/vdclient.json, "
                             "/etc/vdclient/vdclient.json that exists)")
    parser.add_argument("--log-level", metavar="LEVEL",
                        help="override logging.level from the config file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    overrides: dict = {}
    if args.log_level:
        overrides["logging"] = {"level": args.log_level}

    try:
        cfg = config_module.load(args.config, overrides)
    except config_module.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    logging_setup.configure(cfg.logging.level, cfg.logging.timestamps)
    log = logging_setup.get("main")

    # A persistent per-install ConnectionID would normally be generated once and
    # reused -- it is what lets the 17-byte fast-reconnect probe match on later
    # runs. A fresh one each run still pairs correctly.
    connection_id = uuid.uuid4().bytes
    log.info("ConnectionID for this run: %s", connection_id.hex())

    registry = SessionRegistry()

    threads = [
        threading.Thread(
            target=discovery.run,
            args=(connection_id, registry, cfg),
            daemon=True,
        )
    ]
    for port in TCP_PORTS:
        threads.append(
            threading.Thread(target=server.run, args=(port, registry, cfg), daemon=True)
        )

    for t in threads:
        t.start()

    log.info("all listeners started, Ctrl+C to stop")
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log.info("stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
