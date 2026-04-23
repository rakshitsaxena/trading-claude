"""Portfolio-style backtest: multi-symbol gap-reversion vs SPY intraday benchmark.

Design:
  - Universe: sector ETFs + SPY/QQQ/IWM.
  - For each session date:
      1. At 10:00 ET, generate gap-reversion candidates across the universe.
      2. Rule-based allocator: pick top N by conviction, equal-weight.
      3. Enter at each symbol's 10:00 price (close of 09:30 30m bar),
         exit at 15:00 price (close of 14:30 30m bar).
  - Benchmark: SPY return from 10:00 → 15:00 (NOT daily close-to-close).
  - Cost model: 1bp haircut per round-trip per position (spread + slippage).

Output: a summary table to stdout + a JSONL of per-day portfolio returns.

No LLM calls. This is pure validation of the mechanical edge.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from src.data.bars import BarRequest, fetch, group_by_session
from src.signals.gap_reversion import gap_reversion_candidates, Candidate


UNIVERSE = [
    # Sector SPDRs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    # Broad market
    "SPY", "QQQ", "IWM",
]

ENTRY_TIME = pd.Timestamp("10:00").time()
EXIT_BAR_START = pd.Timestamp("14:30").time()  # close of this bar = 15:00

# Round-trip cost (spread + slippage) per position. 1bp = 0.0001.
COST_BPS = 1.0


def load_universe_bars(days: int) -> Dict[str, pd.DataFrame]:
    """Fetch 30m + daily bars for each symbol. Returns {symbol: m30_df}."""
    out: Dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        try:
            m30 = fetch(BarRequest(sym, "30m", days))
            if not m30.empty:
                out[sym] = m30
        except Exception as e:
            print(f"[skip] {sym}: {e}")
    return out


def load_daily_history(days: int) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        try:
            daily = fetch(BarRequest(sym, "1d", max(days + 60, 400)))
            if not daily.empty:
                out[sym] = daily
        except Exception as e:
            print(f"[skip daily] {sym}: {e}")
    return out


def build_symbol_data(
    m30_by_symbol: Dict[str, pd.DataFrame],
    daily_by_symbol: Dict[str, pd.DataFrame],
) -> Dict[str, dict]:
    """For each symbol, build the bundle consumed by the signal function."""
    bundles: Dict[str, dict] = {}
    for sym, m30 in m30_by_symbol.items():
        opens_by_date = {}
        for d, session in group_by_session(m30):
            open_bar = session[session.index.time == pd.Timestamp("09:30").time()]
            if not open_bar.empty:
                opens_by_date[d] = float(open_bar.iloc[0]["open"])
        bundles[sym] = {
            "daily": daily_by_symbol.get(sym, pd.DataFrame()),
            "opens_by_date": opens_by_date,
        }
    return bundles


def entry_exit_prices(session: pd.DataFrame) -> tuple | None:
    entry_rows = session[session.index.time < ENTRY_TIME]
    exit_rows = session[session.index.time == EXIT_BAR_START]
    if entry_rows.empty or exit_rows.empty:
        return None
    entry = float(entry_rows.iloc[-1]["close"])
    exit_ = float(exit_rows.iloc[0]["close"])
    return entry, exit_


def allocate(candidates: List[Candidate], top_n: int) -> List[Candidate]:
    """Equal-weight top N by conviction."""
    return candidates[:top_n]


def run_backtest(days: int, top_n: int, min_abs_z: float) -> dict:
    print(f"[fetch] intraday + daily bars for {len(UNIVERSE)} symbols, {days}d lookback")
    m30_by_symbol = load_universe_bars(days)
    daily_by_symbol = load_daily_history(days)
    bundles = build_symbol_data(m30_by_symbol, daily_by_symbol)

    # Sessions keyed by date — union of all symbols. We iterate SPY dates since
    # SPY sets the benchmark and is always present.
    if "SPY" not in m30_by_symbol:
        raise RuntimeError("SPY 30m data missing — can't proceed")
    spy_sessions = {d: s for d, s in group_by_session(m30_by_symbol["SPY"])}
    dates = sorted(spy_sessions.keys())

    per_day = []  # each: {date, n_trades, port_ret, spy_ret, alpha, picks}
    all_trades = []

    for d in dates:
        # SPY benchmark over the holding window
        spy_prices = entry_exit_prices(spy_sessions[d])
        if spy_prices is None:
            continue
        spy_entry, spy_exit = spy_prices
        spy_ret = spy_exit / spy_entry - 1

        # Gap candidates
        candidates = gap_reversion_candidates(
            as_of_date=d,
            symbol_data=bundles,
            min_abs_z=min_abs_z,
        )
        picks = allocate(candidates, top_n)

        # Compute PnL: equal-weight. Each pick contributes pnl/N to basket return.
        legs = []
        for c in picks:
            if c.symbol not in m30_by_symbol:
                continue
            sess = m30_by_symbol[c.symbol][m30_by_symbol[c.symbol].index.date == d]
            prices = entry_exit_prices(sess)
            if prices is None:
                continue
            e, x = prices
            raw = c.side * (x / e - 1)
            net = raw - (COST_BPS / 10000.0)
            legs.append({
                "symbol": c.symbol, "side": c.side, "conviction": c.conviction,
                "entry": e, "exit": x, "raw_ret": raw, "net_ret": net,
                "reason": c.reason,
            })
            all_trades.append({
                "date": d.isoformat(), **legs[-1],
            })

        port_ret = (sum(l["net_ret"] for l in legs) / len(legs)) if legs else 0.0
        per_day.append({
            "date": d,
            "n_trades": len(legs),
            "port_ret": port_ret,
            "spy_ret": spy_ret,
            "alpha": port_ret - spy_ret,
            "picks": [l["symbol"] + ("+" if l["side"] > 0 else "-") for l in legs],
        })

    return {
        "per_day": per_day,
        "trades": all_trades,
        "universe": UNIVERSE,
        "params": {"days": days, "top_n": top_n, "min_abs_z": min_abs_z,
                   "cost_bps": COST_BPS},
    }


def _sharpe(returns: np.ndarray, ann_factor: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    mu = returns.mean()
    sd = returns.std(ddof=1)
    if sd <= 0:
        return 0.0
    return float((mu / sd) * np.sqrt(ann_factor))


def report(result: dict) -> None:
    per_day = result["per_day"]
    if not per_day:
        print("NO RESULTS")
        return

    traded = [r for r in per_day if r["n_trades"] > 0]
    port_rets = np.array([r["port_ret"] for r in per_day])
    spy_rets = np.array([r["spy_ret"] for r in per_day])
    alphas = port_rets - spy_rets
    active_rets = np.array([r["port_ret"] for r in traded])

    n_days = len(per_day)
    n_traded = len(traded)
    port_cum = float((1 + port_rets).prod() - 1)
    spy_cum = float((1 + spy_rets).prod() - 1)

    max_dd_port = _max_drawdown(port_rets)
    win_days = int((alphas > 0).sum())

    print("\n" + "=" * 60)
    print("  PORTFOLIO GAP-REVERSION BACKTEST")
    print("=" * 60)
    print(f"  params: {result['params']}")
    print(f"  universe ({len(result['universe'])}): {', '.join(result['universe'])}")
    print("-" * 60)
    print(f"  sessions:            {n_days}")
    print(f"  sessions traded:     {n_traded}  ({n_traded/n_days:.0%})")
    print(f"  avg trades/day:      "
          f"{np.mean([r['n_trades'] for r in traded]) if traded else 0:.2f}")
    print("-" * 60)
    print(f"  portfolio cum ret:   {port_cum:+.3%}")
    print(f"  SPY     cum ret:     {spy_cum:+.3%}")
    print(f"  cum alpha:           {port_cum - spy_cum:+.3%}")
    print(f"  avg daily alpha:     {alphas.mean()*10000:+.2f} bps")
    print(f"  alpha hit rate:      {win_days/n_days:.0%}  ({win_days}/{n_days})")
    print("-" * 60)
    print(f"  Sharpe (portfolio):  {_sharpe(port_rets):+.2f}")
    print(f"  Sharpe (SPY intra):  {_sharpe(spy_rets):+.2f}")
    print(f"  Sharpe (alpha):      {_sharpe(alphas):+.2f}")
    print(f"  Sharpe on traded dy: {_sharpe(active_rets):+.2f}")
    print(f"  portfolio vol (ann): {port_rets.std(ddof=1)*np.sqrt(252):.2%}")
    print(f"  max daily loss:      {port_rets.min()*100:+.2f}%")
    print(f"  max drawdown:        {max_dd_port:.2%}")
    print("=" * 60)


def _max_drawdown(rets: np.ndarray) -> float:
    if len(rets) == 0:
        return 0.0
    curve = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    return float(dd.min())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--min-z", type=float, default=1.0)
    ap.add_argument("--out", default="backtest_results/portfolio_gap_reversion.jsonl")
    args = ap.parse_args()

    result = run_backtest(args.days, args.top_n, args.min_z)
    report(result)

    out_path = Path(args.out)
    out_path.parent.mkdir(exist_ok=True)
    with out_path.open("w") as f:
        for r in result["per_day"]:
            row = {**r, "date": r["date"].isoformat()}
            f.write(json.dumps(row) + "\n")
    print(f"\nper-day log: {out_path}")


if __name__ == "__main__":
    main()
