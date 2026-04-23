"""Signal library for the portfolio-era design.

Each signal emits *candidates* (not executed trades). A candidate is:
    { "symbol", "side" (+1/-1), "conviction" (0..1), "source", "reason" }

The allocator picks a subset; Claude (live) or a rule (backtest) decides sizing.
"""
from .gap_reversion import gap_reversion_candidates

__all__ = ["gap_reversion_candidates"]
