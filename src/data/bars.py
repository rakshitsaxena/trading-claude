"""Intraday + daily bar fetching with a parquet cache.

yfinance free-tier limits:
  - 1m bars:  last 7 days only
  - 5/15/30m: last 60 days
  - 1h bars:  last 730 days   <-- our main backtest grain
  - 1d bars:  ~unlimited

We cache per (symbol, interval) in data_cache/<symbol>_<interval>.parquet.
Re-fetch merges on timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

ET = "America/New_York"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)


@dataclass
class BarRequest:
    symbol: str
    interval: str  # "1h", "1d", "30m"
    days: int      # how far back


def _cache_path(symbol: str, interval: str) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}.parquet"


def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Ensure ET-localized DatetimeIndex, standard columns, sorted, deduped."""
    if df.empty:
        return df
    # yfinance returns tz-aware UTC for intraday, tz-naive for daily
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df = df.copy()
    df.index = idx.tz_convert(ET)
    df.index.name = "timestamp"
    # Multi-ticker downloads give MultiIndex columns; we always fetch one symbol
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].rename(
        columns=str.lower
    )
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["symbol"] = symbol
    return df


def fetch(req: BarRequest, use_cache: bool = True) -> pd.DataFrame:
    """Fetch bars; merge with cache on disk."""
    path = _cache_path(req.symbol, req.interval)
    cached = pd.DataFrame()
    if use_cache and path.exists():
        cached = pd.read_parquet(path)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC").tz_convert(ET)

    # yfinance is finicky with start/end on intraday intervals — it sometimes
    # rejects ranges it claims are out-of-window. `period=` is reliable for
    # intraday, and we fall back to start/end for daily+ where period is coarser.
    intraday = req.interval.endswith("m") or req.interval.endswith("h")
    if intraday:
        days = min(req.days, 60 if req.interval.endswith("m") else 730)
        raw = yf.download(
            req.symbol,
            period=f"{days}d",
            interval=req.interval,
            auto_adjust=False,
            progress=False,
            prepost=False,
            threads=False,
        )
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=req.days)
        raw = yf.download(
            req.symbol,
            start=start,
            end=end,
            interval=req.interval,
            auto_adjust=False,
            progress=False,
            prepost=False,
            threads=False,
        )
    fresh = _normalize(raw, req.symbol)

    if cached.empty:
        merged = fresh
    elif fresh.empty:
        merged = cached
    else:
        merged = pd.concat([cached, fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()

    if not merged.empty:
        merged.to_parquet(path)
    return merged


def session_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to regular session 09:30–16:00 ET."""
    if df.empty:
        return df
    t = df.index.time
    mask = (t >= pd.Timestamp("09:30").time()) & (t < pd.Timestamp("16:00").time())
    return df[mask]


def group_by_session(df: pd.DataFrame):
    """Yield (date, session_df) for each trading day, regular hours only."""
    df = session_bars(df)
    for date, g in df.groupby(df.index.date):
        if len(g):
            yield date, g
