#!/usr/bin/env python
"""Scheduler entrypoint for the portfolio flow.

Usage:
  python run_agent.py --slot open      # 10:00 ET: allocator + place orders
  python run_agent.py --slot close     # 15:00 ET: flatten all (no LLM)
  python run_agent.py --slot brief     # 15:05 ET: daily review (cheap LLM call)
  python run_agent.py --slot open --dry-run   # skip LLM + orders
"""
from __future__ import annotations

import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["open", "close", "brief"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        if args.slot == "open":
            from src.agent.allocator import run
            result = run(dry_run=args.dry_run)
            print(json.dumps({
                "decision": result.decision,
                "orders": result.orders_placed,
                "halted": result.halted,
                "history_path": str(result.history_path),
            }, indent=2, default=str))
        elif args.slot == "close":
            from src.agent.closer import run
            result = run(dry_run=args.dry_run)
            print(json.dumps({
                "orders": result.orders,
                "realised_pnl_pct": result.realised_pnl_pct,
                "spy_intraday_pct": result.spy_intraday_pct,
                "alpha_pct": result.alpha_pct,
                "history_path": str(result.history_path),
            }, indent=2, default=str))
        elif args.slot == "brief":
            from src.agent.brief import run
            result = run(dry_run=args.dry_run)
            print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
