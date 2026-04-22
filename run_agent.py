#!/usr/bin/env python
"""Scheduler entrypoint. Invoked at each decision slot.

Usage:
  python run_agent.py --slot open
  python run_agent.py --slot close
  python run_agent.py --slot open --dry-run   # no Claude call, no Telegram
"""
from __future__ import annotations

import argparse
import json
import sys

from src.agent.decide import run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["open", "close"], required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="skip Claude/Telegram; log a FLAT row")
    args = ap.parse_args()

    try:
        result = run(args.slot, dry_run=args.dry_run)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result.decision, indent=2, default=str))
    print(f"\nhistory: {result.history_path}")


if __name__ == "__main__":
    main()
