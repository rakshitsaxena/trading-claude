"""Overnight momentum: if overnight gap AND the 09:30-10:00 bar confirms
the same direction (bar close on gap-side of bar open), ride it.

This is the opposite of gap_fade — we want continuation of overnight flow
but only when the opening range confirms.
"""
from __future__ import annotations

from .base import Strategy, Signal, DecisionContext, FLAT


class OvernightMomentum(Strategy):
    name = "overnight_momentum"

    def __init__(self, min_gap_pct: float = 0.002, size: float = 1.0):
        self.min_gap_pct = min_gap_pct
        self.size = size

    def decide(self, ctx: DecisionContext) -> Signal:
        if ctx.daily_history.empty or ctx.open_window.empty:
            return FLAT
        prev_close = float(ctx.daily_history.iloc[-1]["close"])
        bar = ctx.open_window.iloc[0]
        today_open = float(bar["open"])
        or_close = float(bar["close"])
        gap = (today_open - prev_close) / prev_close
        if abs(gap) < self.min_gap_pct:
            return Signal(0, 0.0, f"gap {gap:+.2%} too small")

        gap_up = gap > 0
        or_up = or_close > today_open
        if gap_up and or_up:
            return Signal(1, self.size, f"momentum long: gap {gap:+.2%}, OR confirms ({today_open:.2f}->{or_close:.2f})")
        if (not gap_up) and (not or_up):
            return Signal(-1, self.size, f"momentum short: gap {gap:+.2%}, OR confirms ({today_open:.2f}->{or_close:.2f})")
        return Signal(0, 0.0, f"gap {gap:+.2%} but OR disagrees ({today_open:.2f}->{or_close:.2f})")
