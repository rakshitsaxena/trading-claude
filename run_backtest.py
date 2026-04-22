#!/usr/bin/env python
"""Backtest one or all strategies against a symbol.

Usage:
  python run_backtest.py --strategy orb --symbol SPY --days 60
  python run_backtest.py --strategy all --symbol SPY --days 60
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.backtest import engine
from src.backtest.metrics import Report, print_reports
from src import strategies


OUT_DIR = Path(__file__).parent / "backtest_results"
OUT_DIR.mkdir(exist_ok=True)


def _save_trades(result: engine.BacktestResult) -> None:
    path = OUT_DIR / f"{result.strategy}_{result.symbol}_trades.jsonl"
    with path.open("w") as f:
        for t in result.trades:
            f.write(json.dumps(t.to_dict()) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="all",
                    help="strategy name or 'all'")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--days", type=int, default=60,
                    help="lookback (30m bars capped at 60d by yfinance)")
    ap.add_argument("--benchmark", default="SPY",
                    help="buy-and-hold benchmark symbol")
    args = ap.parse_args()

    names = strategies.ALL if args.strategy == "all" else [args.strategy]

    reports = []
    session_dates = None
    for name in names:
        strat = strategies.load(name)
        print(f"\n=== {name} on {args.symbol} ({args.days}d) ===")
        result = engine.run(strat, args.symbol, days=args.days)
        _save_trades(result)
        print(f"  trades: {len(result.trades)}  hit_rate: {result.report.hit_rate:.1%}")
        reports.append(result.report)
        if session_dates is None:
            session_dates = list(result.daily_returns.index)

    # Benchmark aligned to the strategy's session dates (apples-to-apples)
    bh = engine.buy_and_hold_benchmark(args.benchmark, days=args.days,
                                       session_dates=session_dates)
    reports.append(bh.report)

    print("\n=== Summary ===")
    print_reports(reports)


if __name__ == "__main__":
    main()
