#!/usr/bin/env python3
"""Server pipeline — run once per invocation (schedule with systemd/cron). python scripts/run_once.py"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from newzyx.run import run_daily_pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Newzyx pipeline once")
    parser.add_argument(
        "-t",
        "--days-ago",
        type=int,
        default=0,
        help="Calendar day for the episode and article news date (0=today, 1=yesterday)",
    )
    args = parser.parse_args()
    raise SystemExit(run_daily_pipeline(t=args.days_ago))
