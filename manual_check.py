#!/usr/bin/env python3
"""Run a one-off seats.aero check from the terminal (blocks until finished)."""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    import monitor

    p = argparse.ArgumentParser(description="Run seats.aero check(s) once without starting the web UI.")
    p.add_argument(
        "--name",
        "-n",
        metavar="ALERT_NAME",
        help="Run a single alert by exact name (runs even if paused).",
    )
    args = p.parse_args()

    alerts = monitor.load_alerts_from_disk()
    if args.name:
        found = next((a for a in alerts if str(a.get("name")) == args.name), None)
        if not found:
            print(f"Unknown alert: {args.name!r}", file=sys.stderr)
            return 1
        monitor.run_single_alert(found)
        return 0

    ran = 0
    for a in alerts:
        if a.get("enabled", True):
            monitor.run_single_alert(a)
            ran += 1
    if ran == 0:
        print("No enabled alerts in alerts.yaml.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
