"""A/B backtest: same gap-reversion strategy, with vs without the regime filter.

Regime policy:
  - "trend"  : SKIP all gap fades (respect the drift)
  - "chop"   : full-size gap fades (noise is our friend)
  - "neutral": normal gap fades

Compares key metrics side-by-side and breaks performance down by regime day.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import numpy as np
import pandas as pd

from src.data.bars import BarRequest, fetch, group_by_session
from src.signals.gap_reversion import gap_reversion_candidates
from src.regime import classify
from scripts.backtest_portfolio import (
    UNIVERSE, load_universe_bars, load_daily_history,
    build_symbol_data, entry_exit_prices, COST_BPS,
)


def run(days: int, top_n: int, min_z: float, use_regime: bool,
        vix_data: pd.DataFrame, m30_by_symbol, bundles) -> dict:
    spy_sessions = {d: s for d, s in group_by_session(m30_by_symbol["SPY"])}
    dates = sorted(spy_sessions.keys())

    per_day = []
    spy_daily = bundles["SPY"]["daily"]

    for d in dates:
        prices = entry_exit_prices(spy_sessions[d])
        if prices is None:
            continue
        se, sx = prices
        spy_ret = sx / se - 1

        reg = classify(d, spy_daily, vix_data)

        # Filter policy
        if use_regime and reg.regime == "trend":
            per_day.append({"date": d, "regime": reg.regime, "n_trades": 0,
                            "port_ret": 0.0, "spy_ret": spy_ret,
                            "picks": [], "skipped": True})
            continue

        cands = gap_reversion_candidates(d, bundles, min_abs_z=min_z)
        legs = []
        for c in cands[:top_n]:
            if c.symbol not in m30_by_symbol:
                continue
            sess = m30_by_symbol[c.symbol][m30_by_symbol[c.symbol].index.date == d]
            p = entry_exit_prices(sess)
            if p is None:
                continue
            e, x = p
            net = c.side * (x / e - 1) - COST_BPS / 10000
            legs.append({"sym": c.symbol, "side": c.side, "net": net})

        port_ret = (sum(l["net"] for l in legs) / len(legs)) if legs else 0.0
        per_day.append({
            "date": d, "regime": reg.regime,
            "n_trades": len(legs),
            "port_ret": port_ret, "spy_ret": spy_ret,
            "picks": [l["sym"] + ("+" if l["side"] > 0 else "-") for l in legs],
            "skipped": False,
        })
    return per_day


def summarise(per_day, label: str) -> dict:
    port = np.array([r["port_ret"] for r in per_day])
    spy = np.array([r["spy_ret"] for r in per_day])
    alpha = port - spy

    n = len(port)
    traded = sum(1 for r in per_day if r["n_trades"] > 0)
    cum_port = float((1 + port).prod() - 1)
    cum_spy = float((1 + spy).prod() - 1)
    sharpe = ((port.mean() / port.std(ddof=1)) * np.sqrt(252)
              if port.std(ddof=1) > 0 else 0.0)
    alpha_sharpe = ((alpha.mean() / alpha.std(ddof=1)) * np.sqrt(252)
                    if alpha.std(ddof=1) > 0 else 0.0)
    curve = np.cumprod(1 + port)
    dd = ((curve - np.maximum.accumulate(curve)) / np.maximum.accumulate(curve)).min()

    # Regime breakdown
    by_regime = defaultdict(list)
    for r in per_day:
        by_regime[r["regime"]].append(r["port_ret"])

    return {
        "label": label,
        "n": n,
        "traded": traded,
        "cum_port": cum_port,
        "cum_spy": cum_spy,
        "cum_alpha": cum_port - cum_spy,
        "mean_bps": port.mean() * 10000,
        "alpha_bps": alpha.mean() * 10000,
        "sharpe": sharpe,
        "alpha_sharpe": alpha_sharpe,
        "max_dd": dd,
        "vol_ann": port.std(ddof=1) * np.sqrt(252),
        "by_regime": {k: {
            "n_days": len(v),
            "mean_bps": np.mean(v) * 10000,
            "cum": float((1 + np.array(v)).prod() - 1),
        } for k, v in by_regime.items()},
    }


def pct(x): return f"{x:+.2%}"


def print_side_by_side(a: dict, b: dict) -> None:
    rows = [
        ("sessions", f"{a['n']}", f"{b['n']}"),
        ("sessions traded", f"{a['traded']}", f"{b['traded']}"),
        ("cum portfolio ret", pct(a["cum_port"]), pct(b["cum_port"])),
        ("cum alpha vs SPY", pct(a["cum_alpha"]), pct(b["cum_alpha"])),
        ("avg daily ret (bps)", f"{a['mean_bps']:+.1f}", f"{b['mean_bps']:+.1f}"),
        ("avg daily alpha (bps)", f"{a['alpha_bps']:+.1f}", f"{b['alpha_bps']:+.1f}"),
        ("Sharpe (port)", f"{a['sharpe']:+.2f}", f"{b['sharpe']:+.2f}"),
        ("Sharpe (alpha)", f"{a['alpha_sharpe']:+.2f}", f"{b['alpha_sharpe']:+.2f}"),
        ("ann vol", f"{a['vol_ann']:.1%}", f"{b['vol_ann']:.1%}"),
        ("max drawdown", f"{a['max_dd']:+.2%}", f"{b['max_dd']:+.2%}"),
    ]
    print(f"\n{'metric':24s} {a['label']:>18s}   {b['label']:>18s}")
    print("-" * 66)
    for name, va, vb in rows:
        print(f"{name:24s} {va:>18s}   {vb:>18s}")

    print("\n--- regime day breakdown ---")
    for d in (a, b):
        print(f"\n[{d['label']}]")
        for reg, stats in sorted(d["by_regime"].items()):
            print(f"  {reg:8s} n={stats['n_days']:3d}  "
                  f"mean={stats['mean_bps']:+7.1f}bps  cum={stats['cum']:+.2%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--min-z", type=float, default=1.0)
    args = ap.parse_args()

    print(f"[fetch] {len(UNIVERSE)} symbols, {args.days}d")
    m30 = load_universe_bars(args.days)
    daily = load_daily_history(args.days)
    bundles = build_symbol_data(m30, daily)
    vix = fetch(BarRequest("^VIX", "1d", 400))

    print("\n[A] baseline (no regime filter)")
    per_day_a = run(args.days, args.top_n, args.min_z, False, vix, m30, bundles)
    print("[B] with regime filter (skip trend days)")
    per_day_b = run(args.days, args.top_n, args.min_z, True,  vix, m30, bundles)

    sum_a = summarise(per_day_a, "baseline")
    sum_b = summarise(per_day_b, "regime-filtered")
    print_side_by_side(sum_a, sum_b)


if __name__ == "__main__":
    main()
