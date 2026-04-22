"""Gap fade: if today opens with a >threshold gap vs yesterday's close,
bet the gap closes (fade it) by 15:00 ET.

Long if down-gap, short if up-gap, flat if gap too small.
Rationale: large overnight gaps often partially retrace during the day.
"""
from __future__ import annotations

from .base import Strategy, Signal, DecisionContext, FLAT


class GapFade(Strategy):
    name = "gap_fade"

    def __init__(self, min_gap_pct: float = 0.003, max_gap_pct: float = 0.02,
                 size: float = 1.0):
        # Trade gaps between 0.3% and 2.0%; outside that is noise or crisis.
        self.min_gap_pct = min_gap_pct
        self.max_gap_pct = max_gap_pct
        self.size = size

    def decide(self, ctx: DecisionContext) -> Signal:
        if ctx.daily_history.empty or ctx.open_window.empty:
            return FLAT
        prev_close = float(ctx.daily_history.iloc[-1]["close"])
        today_open = float(ctx.open_window.iloc[0]["open"])
        gap = (today_open - prev_close) / prev_close
        abs_gap = abs(gap)
        if abs_gap < self.min_gap_pct or abs_gap > self.max_gap_pct:
            return Signal(0, 0.0, f"gap {gap:+.2%} outside trade band")
        if gap > 0:
            return Signal(-1, self.size, f"fade up-gap {gap:+.2%} (prev {prev_close:.2f} -> open {today_open:.2f})")
        return Signal(1, self.size, f"fade down-gap {gap:+.2%} (prev {prev_close:.2f} -> open {today_open:.2f})")
