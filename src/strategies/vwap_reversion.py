"""VWAP reversion: at 10:00 ET, if the OR close is >N*ATR from the OR VWAP,
fade toward VWAP.

Rationale: morning overextension often reverts to midday as volume catches up.
Uses a short ATR proxy (daily range over last 14 days) for normalization.
"""
from __future__ import annotations

import numpy as np

from .base import Strategy, Signal, DecisionContext, FLAT


def _atr(daily_history, n: int = 14) -> float:
    if len(daily_history) < 2:
        return 0.0
    tail = daily_history.tail(n + 1)
    hi = tail["high"].values
    lo = tail["low"].values
    close = tail["close"].values
    tr = np.maximum(hi[1:] - lo[1:],
                    np.maximum(np.abs(hi[1:] - close[:-1]),
                               np.abs(lo[1:] - close[:-1])))
    return float(tr.mean()) if len(tr) else 0.0


class VWAPReversion(Strategy):
    name = "vwap_reversion"

    def __init__(self, min_sigma: float = 0.15, size: float = 1.0):
        self.min_sigma = min_sigma
        self.size = size

    def decide(self, ctx: DecisionContext) -> Signal:
        bars = ctx.open_window
        if bars.empty or ctx.daily_history.empty:
            return FLAT
        # VWAP of the opening window (typical price weighted by volume)
        tp = (bars["high"] + bars["low"] + bars["close"]) / 3
        vol = bars["volume"].replace(0, np.nan).fillna(1)
        vwap = float((tp * vol).sum() / vol.sum())
        last_close = float(bars.iloc[-1]["close"])
        atr = _atr(ctx.daily_history)
        if atr == 0:
            return FLAT
        dev_sigma = (last_close - vwap) / atr
        if dev_sigma > self.min_sigma:
            return Signal(-1, self.size, f"fade above VWAP: close {last_close:.2f} > vwap {vwap:.2f} ({dev_sigma:+.2f} ATR)")
        if dev_sigma < -self.min_sigma:
            return Signal(1, self.size, f"fade below VWAP: close {last_close:.2f} < vwap {vwap:.2f} ({dev_sigma:+.2f} ATR)")
        return Signal(0, 0.0, f"close near VWAP ({dev_sigma:+.2f} ATR)")
