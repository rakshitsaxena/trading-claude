"""Three sanity checks on the portfolio gap-reversion backtest:
  1. Flip signal: if we RIDE gaps instead of FADE, do we lose ~symmetrically?
     (If flipped also wins, there's a data-leak or timing bug.)
  2. In-sample / out-of-sample split: first half → baseline, second half → test.
  3. Spot-check one trade's arithmetic end-to-end against the raw bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.bars import BarRequest, fetch, group_by_session
from src.signals.gap_reversion import gap_reversion_candidates
from scripts.backtest_portfolio import (
    UNIVERSE, load_universe_bars, load_daily_history,
    build_symbol_data, entry_exit_prices, COST_BPS,
)


def run(flip: bool, start_pct: float = 0.0, end_pct: float = 1.0,
        top_n: int = 3, min_z: float = 1.0, days: int = 60):
    m30_by_symbol = load_universe_bars(days)
    daily_by_symbol = load_daily_history(days)
    bundles = build_symbol_data(m30_by_symbol, daily_by_symbol)

    spy_sessions = {d: s for d, s in group_by_session(m30_by_symbol["SPY"])}
    dates = sorted(spy_sessions.keys())
    i0 = int(len(dates) * start_pct)
    i1 = int(len(dates) * end_pct)
    dates = dates[i0:i1]

    port, spy = [], []
    for d in dates:
        prices = entry_exit_prices(spy_sessions[d])
        if prices is None:
            continue
        se, sx = prices
        spy_ret = sx / se - 1

        cands = gap_reversion_candidates(d, bundles, min_abs_z=min_z)
        legs = []
        for c in cands[:top_n]:
            sess = m30_by_symbol[c.symbol][m30_by_symbol[c.symbol].index.date == d]
            p = entry_exit_prices(sess)
            if p is None:
                continue
            e, x = p
            side = c.side * (-1 if flip else 1)
            legs.append(side * (x / e - 1) - COST_BPS / 10000)
        port.append(np.mean(legs) if legs else 0.0)
        spy.append(spy_ret)

    port = np.array(port)
    spy = np.array(spy)
    alpha = port - spy
    return {
        "n": len(port),
        "n_traded": int((port != 0).sum()),
        "cum_port": float((1 + port).prod() - 1),
        "cum_spy": float((1 + spy).prod() - 1),
        "mean_port_bps": float(port.mean() * 10000),
        "mean_spy_bps": float(spy.mean() * 10000),
        "alpha_bps_avg": float(alpha.mean() * 10000),
        "sharpe_port": float((port.mean() / port.std(ddof=1) * np.sqrt(252))
                             if port.std() > 0 else 0),
        "alpha_hit_rate": float((alpha > 0).mean()),
    }


def main():
    print("\n[1] Signal polarity check")
    print("-" * 60)
    fade = run(flip=False)
    ride = run(flip=True)
    for label, r in [("FADE (original)", fade), ("RIDE (flipped)", ride)]:
        print(f"  {label:20s}  cum={r['cum_port']:+.2%}  "
              f"mean={r['mean_port_bps']:+.1f}bps  Sharpe={r['sharpe_port']:+.2f}")
    print(f"  (real edge would show FADE ≫ 0 and RIDE ≪ 0, roughly symmetric)")

    print("\n[2] In-sample / out-of-sample split (first 50% vs last 50%)")
    print("-" * 60)
    first = run(flip=False, start_pct=0.0, end_pct=0.5)
    second = run(flip=False, start_pct=0.5, end_pct=1.0)
    for label, r in [("First half ", first), ("Second half", second)]:
        print(f"  {label}  n={r['n']:3d}  cum={r['cum_port']:+.2%}  "
              f"mean={r['mean_port_bps']:+.1f}bps  Sharpe={r['sharpe_port']:+.2f}  "
              f"alpha_hit={r['alpha_hit_rate']:.0%}")

    print("\n[3] Manual arithmetic spot-check — one specific trade")
    print("-" * 60)
    days = 60
    m30 = load_universe_bars(days)
    daily = load_daily_history(days)
    bundles = build_symbol_data(m30, daily)
    # Grab any day with candidates
    spy_sessions = {d: s for d, s in group_by_session(m30["SPY"])}
    checked = False
    for d in sorted(spy_sessions.keys())[-10:]:
        cands = gap_reversion_candidates(d, bundles, min_abs_z=1.0)
        if not cands:
            continue
        c = cands[0]
        sym = c.symbol
        sess = m30[sym][m30[sym].index.date == d]
        daily_sym = daily[sym]
        prev_close = float(
            daily_sym[daily_sym.index.date < d]["close"].iloc[-1]
        )
        today_open = float(
            sess[sess.index.time == pd.Timestamp("09:30").time()].iloc[0]["open"]
        )
        entry = float(sess[sess.index.time < pd.Timestamp("10:00").time()].iloc[-1]["close"])
        exit_ = float(sess[sess.index.time == pd.Timestamp("14:30").time()].iloc[0]["close"])
        raw_ret = c.side * (exit_ / entry - 1)
        print(f"  date={d}  symbol={sym}  side={'+' if c.side>0 else '-'}  "
              f"conv={c.conviction:.2f}")
        print(f"    prev_close  = {prev_close:.4f}")
        print(f"    today_open  = {today_open:.4f}  → gap={today_open/prev_close-1:+.2%}")
        print(f"    entry(10:00)= {entry:.4f}")
        print(f"    exit (15:00)= {exit_:.4f}  → move={exit_/entry-1:+.2%}")
        print(f"    side*move   = {raw_ret*100:+.2f}%  (net after 1bp: "
              f"{(raw_ret - 1e-4)*100:+.2f}%)")
        print(f"    signal says: {c.reason}")
        checked = True
        break
    if not checked:
        print("  no candidates found in recent 10 days")


if __name__ == "__main__":
    main()
