"""Gap reversion signal.

Thesis: overnight gaps in liquid ETFs partially mean-revert during the RTH session.
We z-score today's gap vs the symbol's own 30d distribution of gaps and only fire
when |z| exceeds a threshold — avoids the "every gap looks tradeable" trap.

Signal (as seen at 10:00 ET):
  gap_pct = today's 09:30 open / prior close - 1
  z       = gap_pct standardised vs last 30d of gap_pct

Candidate:
  side = -sign(z)           # fade the gap
  conviction = min(|z|/3, 1)

Usable both live (give it `as_of_date`) and in backtest (loop over dates).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List

import numpy as np
import pandas as pd


@dataclass
class Candidate:
    symbol: str
    side: int           # +1 long, -1 short
    conviction: float   # 0..1
    source: str
    reason: str
    # Debug/explanation fields — safe to ignore downstream.
    extra: dict

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "conviction": round(self.conviction, 3),
            "source": self.source,
            "reason": self.reason,
            "extra": self.extra,
        }


def _gap_series(daily: pd.DataFrame, intraday_open_by_date: dict) -> pd.Series:
    """For each date in intraday_open_by_date, compute (open - prev_close) / prev_close.
    Returns a Series indexed by date, aligned to the intraday dates we have."""
    if daily.empty:
        return pd.Series(dtype=float)
    closes = daily["close"]
    closes.index = [t.date() if hasattr(t, "date") else t for t in closes.index]
    gaps = {}
    dates_sorted = sorted(intraday_open_by_date.keys())
    for d in dates_sorted:
        prior = closes[closes.index < d]
        if prior.empty:
            continue
        prev_close = float(prior.iloc[-1])
        today_open = intraday_open_by_date[d]
        if prev_close > 0:
            gaps[d] = (today_open - prev_close) / prev_close
    return pd.Series(gaps).sort_index()


def gap_reversion_candidates(
    as_of_date: date,
    symbol_data: dict,
    *,
    min_abs_z: float = 1.0,
    lookback: int = 30,
) -> List[Candidate]:
    """Generate fade-the-gap candidates for `as_of_date` across the provided symbols.

    Args:
      as_of_date: the trading date we're deciding on (entry is 10:00 ET that day).
      symbol_data: {symbol: {"daily": DataFrame of daily bars (≤ as_of_date-1 needed),
                              "opens_by_date": {date: today_open_price}}}
      min_abs_z: minimum |z-score| of today's gap vs recent history to fire.
      lookback: days of gap history used to z-score.
    """
    out: List[Candidate] = []
    for symbol, bundle in symbol_data.items():
        daily = bundle["daily"]
        opens_by_date = bundle["opens_by_date"]
        if as_of_date not in opens_by_date:
            continue

        gap_hist = _gap_series(daily, opens_by_date)
        if as_of_date not in gap_hist.index:
            continue

        history = gap_hist[gap_hist.index < as_of_date].tail(lookback)
        if len(history) < max(10, lookback // 2):
            continue

        today_gap = float(gap_hist.loc[as_of_date])
        mu = float(history.mean())
        sigma = float(history.std(ddof=1))
        if sigma <= 0 or not np.isfinite(sigma):
            continue
        z = (today_gap - mu) / sigma

        if abs(z) < min_abs_z:
            continue

        side = -1 if z > 0 else 1  # fade
        conviction = min(abs(z) / 3.0, 1.0)
        out.append(Candidate(
            symbol=symbol,
            side=side,
            conviction=conviction,
            source="gap_reversion",
            reason=(f"fade gap {today_gap:+.2%} (z={z:+.2f}) vs "
                    f"{lookback}d μ={mu:+.2%} σ={sigma:.2%}"),
            extra={"gap_pct": today_gap, "z": z, "mu": mu, "sigma": sigma,
                   "n_history": len(history)},
        ))

    # Rank highest conviction first.
    out.sort(key=lambda c: -c.conviction)
    return out
