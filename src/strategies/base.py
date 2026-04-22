"""Strategy interface. Each strategy decides once per day at the open slot."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class Signal:
    direction: int   # -1 short, 0 flat, 1 long
    size: float      # 0.0 to 1.0 (fraction of allowable capital)
    reason: str      # human-readable rationale


FLAT = Signal(0, 0.0, "no signal")


@dataclass
class DecisionContext:
    """Everything a strategy sees at the 10:00 ET open slot for a given day."""
    as_of: pd.Timestamp            # 10:00 ET timestamp for `date`
    symbol: str
    open_window: pd.DataFrame      # 30m bars from 09:30 up to (but not including) 10:00
    daily_history: pd.DataFrame    # daily bars strictly BEFORE today (most-recent last)
    vix_history: Optional[pd.DataFrame] = None  # daily ^VIX bars, same convention


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def decide(self, ctx: DecisionContext) -> Signal:
        ...
