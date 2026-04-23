"""Daily regime snapshot.

At 10:00 ET on date D, classify the market into one of:
  - "trend"   : strong directional drift → gap fades are dangerous (trends persist)
  - "chop"    : high vol, no direction  → gap fades should work well
  - "neutral" : the muddle middle

Signals used (all strictly based on daily bars BEFORE D — no intraday leak):
  - SPY 5-day cumulative return       (trend magnitude)
  - SPY "directional persistence"     = sum(r5) / sum(|r5|) over last 5 sessions
  - SPY 10-day annualised vol         (regime vol)
  - VIX level (if provided)           (cross-check)

Rules (simple + explainable):
  trend   if |5d_ret| >= 2.5% AND |persistence| >= 0.7  (directional & monotone)
          OR VIX <= 13 AND |5d_ret| >= 1.5%              (low-vol grind)
  chop    if VIX >= 20
          OR 10d_vol >= 20% annualised
  neutral otherwise

Callers use the classification to:
  - suppress gap fades in "trend" (or raise z-score hurdle)
  - apply gap fades normally in "chop"/"neutral"
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class RegimeSnapshot:
    regime: str              # "trend" | "chop" | "neutral"
    spy_5d_ret: float
    spy_directional_persistence: float  # -1..+1
    spy_10d_vol_ann: float
    vix_level: Optional[float]
    reason: str

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "spy_5d_ret": round(self.spy_5d_ret, 4),
            "spy_dir_persistence": round(self.spy_directional_persistence, 3),
            "spy_10d_vol_ann": round(self.spy_10d_vol_ann, 4),
            "vix_level": (round(self.vix_level, 2) if self.vix_level is not None
                          else None),
            "reason": self.reason,
        }


def _daily_closes_before(daily: pd.DataFrame, as_of: date) -> pd.Series:
    closes = daily["close"].copy()
    closes.index = [t.date() if hasattr(t, "date") else t for t in closes.index]
    return closes[closes.index < as_of]


def classify(as_of: date, spy_daily: pd.DataFrame,
             vix_daily: Optional[pd.DataFrame] = None) -> RegimeSnapshot:
    spy = _daily_closes_before(spy_daily, as_of)
    if len(spy) < 11:
        return RegimeSnapshot(
            regime="neutral",
            spy_5d_ret=0.0, spy_directional_persistence=0.0,
            spy_10d_vol_ann=0.0, vix_level=None,
            reason="insufficient SPY history",
        )

    tail = spy.tail(11)                   # last 11 closes → 10 daily rets
    rets = tail.pct_change().dropna()
    r5 = rets.tail(5)

    spy_5d_ret = float((tail.iloc[-1] / tail.iloc[-6]) - 1)
    sum_r5 = float(r5.sum())
    sum_abs_r5 = float(r5.abs().sum())
    persistence = (sum_r5 / sum_abs_r5) if sum_abs_r5 > 0 else 0.0

    vol10 = float(rets.std(ddof=1) * np.sqrt(252))

    vix_level: Optional[float] = None
    if vix_daily is not None and not vix_daily.empty:
        vix_closes = _daily_closes_before(vix_daily, as_of)
        if not vix_closes.empty:
            vix_level = float(vix_closes.iloc[-1])

    # Classify
    is_trend_a = abs(spy_5d_ret) >= 0.025 and abs(persistence) >= 0.7
    is_trend_b = (vix_level is not None and vix_level <= 13
                  and abs(spy_5d_ret) >= 0.015)
    is_chop_a = vix_level is not None and vix_level >= 20
    is_chop_b = vol10 >= 0.20

    if is_trend_a or is_trend_b:
        regime = "trend"
        reason = (f"trend: 5d_ret={spy_5d_ret:+.2%}, "
                  f"persistence={persistence:+.2f}, vix={vix_level}")
    elif is_chop_a or is_chop_b:
        regime = "chop"
        reason = f"chop: vol10={vol10:.1%}, vix={vix_level}"
    else:
        regime = "neutral"
        reason = (f"neutral: 5d_ret={spy_5d_ret:+.2%}, "
                  f"vol10={vol10:.1%}, vix={vix_level}")

    return RegimeSnapshot(
        regime=regime,
        spy_5d_ret=spy_5d_ret,
        spy_directional_persistence=persistence,
        spy_10d_vol_ann=vol10,
        vix_level=vix_level,
        reason=reason,
    )
