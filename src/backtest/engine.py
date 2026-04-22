"""Walk-forward intraday backtest for 10:00-enter / 15:00-exit strategies.

Model:
  - At each trading day, feed strategy the 09:30-10:00 bar + daily history up to yesterday.
  - Enter at the close of the 09:30-10:00 bar (i.e. price at 10:00 ET).
  - Exit at the close of the 14:30-15:00 bar (i.e. price at 15:00 ET).
  - Intraday flat: no overnight exposure, no intra-day stops (only 2 signals/day).

Assumes 30m bars (yfinance grain that aligns cleanly at :00/:30 boundaries).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from typing import List, Optional

import pandas as pd

from ..data.bars import BarRequest, fetch, group_by_session, ET
from ..strategies.base import Strategy, DecisionContext, Signal
from .metrics import Report, build_report


ENTRY_TIME = pd.Timestamp("10:00").time()   # close of 09:30 30m bar
EXIT_BAR_START = pd.Timestamp("14:30").time()  # close of this bar = 15:00


@dataclass
class Trade:
    date: date
    symbol: str
    direction: int
    size: float
    entry_price: float
    exit_price: float
    pnl_pct: float
    reason: str

    def to_dict(self):
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    trades: List[Trade]
    daily_returns: pd.Series   # indexed by session date
    report: Report


def _daily_returns_from_trades(trades: List[Trade], all_dates: List[date]) -> pd.Series:
    pnl_by_date = {t.date: t.pnl_pct for t in trades}
    return pd.Series({d: pnl_by_date.get(d, 0.0) for d in all_dates})


def run(strategy: Strategy, symbol: str, days: int = 60,
        vix_symbol: str = "^VIX") -> BacktestResult:
    m30 = fetch(BarRequest(symbol, "30m", days))
    if m30.empty:
        raise RuntimeError(f"No 30m data for {symbol}")

    daily = fetch(BarRequest(symbol, "1d", max(days + 60, 400)))
    try:
        vix = fetch(BarRequest(vix_symbol, "1d", max(days + 60, 400)))
    except Exception:
        vix = pd.DataFrame()

    trades: List[Trade] = []
    session_dates: List[date] = []

    for d, session in group_by_session(m30):
        session_dates.append(d)

        open_window = session[session.index.time < ENTRY_TIME]
        if open_window.empty:
            continue
        entry_price = float(open_window.iloc[-1]["close"])

        exit_rows = session[session.index.time == EXIT_BAR_START]
        if exit_rows.empty:
            continue
        exit_price = float(exit_rows.iloc[0]["close"])

        daily_hist = daily[daily.index.date < d] if not daily.empty else pd.DataFrame()
        vix_hist = vix[vix.index.date < d] if not vix.empty else pd.DataFrame()

        ctx = DecisionContext(
            as_of=pd.Timestamp.combine(d, ENTRY_TIME).tz_localize(ET),
            symbol=symbol,
            open_window=open_window,
            daily_history=daily_hist,
            vix_history=vix_hist,
        )
        signal: Signal = strategy.decide(ctx)
        if signal.direction == 0 or signal.size <= 0:
            continue

        pnl_pct = signal.direction * (exit_price / entry_price - 1) * signal.size
        trades.append(Trade(
            date=d, symbol=symbol,
            direction=signal.direction, size=signal.size,
            entry_price=entry_price, exit_price=exit_price,
            pnl_pct=pnl_pct, reason=signal.reason,
        ))

    daily_returns = _daily_returns_from_trades(trades, session_dates)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    hit_rate = wins / len(trades) if trades else 0.0
    report = build_report(strategy.name, daily_returns, len(trades), hit_rate)

    return BacktestResult(
        strategy=strategy.name,
        symbol=symbol,
        trades=trades,
        daily_returns=daily_returns,
        report=report,
    )


def buy_and_hold_benchmark(symbol: str, days: int,
                           session_dates: Optional[list] = None) -> BacktestResult:
    """Benchmark: close-to-close buy-and-hold. If session_dates is given,
    restrict to exactly those dates for apples-to-apples comparison."""
    daily = fetch(BarRequest(symbol, "1d", days))
    if daily.empty:
        raise RuntimeError(f"No daily data for {symbol}")
    rets = daily["close"].pct_change().dropna()
    rets.index = [t.date() for t in rets.index]
    if session_dates is not None:
        wanted = set(session_dates)
        rets = rets[[d in wanted for d in rets.index]]
    report = build_report(f"{symbol} buy&hold", rets, n_trades=1,
                          hit_rate=float((rets > 0).mean()))
    return BacktestResult(
        strategy=f"{symbol} buy&hold",
        symbol=symbol,
        trades=[],
        daily_returns=rets,
        report=report,
    )
