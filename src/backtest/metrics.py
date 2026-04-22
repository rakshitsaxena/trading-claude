"""Return/risk metrics. All Sharpe/Sortino are annualized assuming 252 trading days."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd


TRADING_DAYS = 252
RF_ANNUAL = 0.045  # rough cash rate; subtract daily equivalent for Sharpe


def _daily_excess(returns: pd.Series) -> pd.Series:
    rf_daily = RF_ANNUAL / TRADING_DAYS
    return returns - rf_daily


def sharpe(returns: pd.Series) -> float:
    if returns.empty or returns.std(ddof=1) == 0:
        return 0.0
    ex = _daily_excess(returns)
    return float(ex.mean() / ex.std(ddof=1) * np.sqrt(TRADING_DAYS))


def sortino(returns: pd.Series) -> float:
    if returns.empty or (returns == 0).all():
        return 0.0
    ex = _daily_excess(returns)
    downside = ex[ex < 0]
    if len(downside) < 2:
        return 0.0
    dd_std = downside.std(ddof=1)
    if dd_std == 0 or not np.isfinite(dd_std):
        return 0.0
    return float(ex.mean() / dd_std * np.sqrt(TRADING_DAYS))


def max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    curve = (1 + returns).cumprod()
    peak = curve.cummax()
    dd = curve / peak - 1
    return float(dd.min())


def total_return(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    return float((1 + returns).prod() - 1)


@dataclass
class Report:
    name: str
    n_days: int
    n_trades: int
    hit_rate: float
    total_return: float
    ann_return: float
    sharpe: float
    sortino: float
    max_dd: float

    def as_row(self) -> dict:
        return {
            "name": self.name,
            "days": self.n_days,
            "trades": self.n_trades,
            "hit_rate": round(self.hit_rate, 3),
            "total_return": round(self.total_return, 4),
            "ann_return": round(self.ann_return, 4),
            "sharpe": round(self.sharpe, 2),
            "sortino": round(self.sortino, 2),
            "max_dd": round(self.max_dd, 4),
        }


def build_report(name: str, daily_returns: pd.Series, n_trades: int, hit_rate: float) -> Report:
    n_days = len(daily_returns)
    tr = total_return(daily_returns)
    ann = (1 + tr) ** (TRADING_DAYS / max(n_days, 1)) - 1 if n_days else 0.0
    return Report(
        name=name,
        n_days=n_days,
        n_trades=n_trades,
        hit_rate=hit_rate,
        total_return=tr,
        ann_return=ann,
        sharpe=sharpe(daily_returns),
        sortino=sortino(daily_returns),
        max_dd=max_drawdown(daily_returns),
    )


def print_reports(reports: List[Report]) -> None:
    df = pd.DataFrame([r.as_row() for r in reports])
    print(df.to_string(index=False))
