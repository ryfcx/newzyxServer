#!/usr/bin/env python3
"""Server pipeline — run once per invocation (schedule with systemd/cron). python scripts/run_once.py"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from newzyx.run import run_daily_pipeline

if __name__ == "__main__":
    raise SystemExit(run_daily_pipeline())
