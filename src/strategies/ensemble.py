"""Regime-gated ensemble.

- Low VIX (<18) → prefer mean-reversion (vwap_reversion, gap_fade)
- High VIX (>=25) → prefer momentum (orb, overnight_momentum)
- In between → stay flat

This is a basic blueprint; Claude can override in the live agent by reading
backtest results of each constituent and picking dynamically.
"""
from __future__ import annotations

from .base import Strategy, Signal, DecisionContext, FLAT
from .orb import ORB
from .gap_fade import GapFade
from .overnight_momentum import OvernightMomentum
from .vwap_reversion import VWAPReversion


class Ensemble(Strategy):
    name = "ensemble"

    def __init__(self, low_vix: float = 18.0, high_vix: float = 25.0):
        self.low_vix = low_vix
        self.high_vix = high_vix
        self.reversion = [VWAPReversion(), GapFade()]
        self.momentum = [ORB(), OvernightMomentum()]

    def _latest_vix(self, ctx: DecisionContext) -> float | None:
        if ctx.vix_history is None or ctx.vix_history.empty:
            return None
        return float(ctx.vix_history.iloc[-1]["close"])

    def _first_signal(self, strategies: list, ctx: DecisionContext) -> Signal:
        for s in strategies:
            sig = s.decide(ctx)
            if sig.direction != 0 and sig.size > 0:
                return Signal(sig.direction, sig.size,
                              f"[{s.name}] {sig.reason}")
        return FLAT

    def decide(self, ctx: DecisionContext) -> Signal:
        vix = self._latest_vix(ctx)
        if vix is None:
            # No VIX data; abstain.
            return Signal(0, 0.0, "no VIX; flat")
        if vix < self.low_vix:
            sig = self._first_signal(self.reversion, ctx)
            return Signal(sig.direction, sig.size,
                          f"VIX {vix:.1f} (low, reversion regime) | {sig.reason}")
        if vix >= self.high_vix:
            sig = self._first_signal(self.momentum, ctx)
            return Signal(sig.direction, sig.size,
                          f"VIX {vix:.1f} (high, momentum regime) | {sig.reason}")
        return Signal(0, 0.0, f"VIX {vix:.1f} (mid regime, flat)")
