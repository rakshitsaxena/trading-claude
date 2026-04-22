"""Opening Range Breakout.

At 10:00 ET we have the 09:30-10:00 30m bar (the opening range).
Decision: long if close of OR > OR midpoint + 0.2*range, short if <, else flat.
Classic momentum continuation from the opening burst.
"""
from __future__ import annotations

from .base import Strategy, Signal, DecisionContext, FLAT


class ORB(Strategy):
    name = "orb"

    def __init__(self, edge_pct: float = 0.2, size: float = 1.0):
        self.edge_pct = edge_pct
        self.size = size

    def decide(self, ctx: DecisionContext) -> Signal:
        bars = ctx.open_window
        if bars.empty:
            return FLAT
        bar = bars.iloc[-1]
        hi, lo, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        rng = hi - lo
        if rng <= 0:
            return FLAT
        mid = (hi + lo) / 2
        upper = mid + self.edge_pct * rng
        lower = mid - self.edge_pct * rng

        if close >= upper:
            return Signal(1, self.size, f"ORB long: close {close:.2f} > upper {upper:.2f} (OR {lo:.2f}-{hi:.2f})")
        if close <= lower:
            return Signal(-1, self.size, f"ORB short: close {close:.2f} < lower {lower:.2f} (OR {lo:.2f}-{hi:.2f})")
        return Signal(0, 0.0, f"ORB flat: close {close:.2f} inside range")
