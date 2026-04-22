"""Alpaca paper client — read-only. We never place orders; user executes manually.

Used by the agent to see current positions and equity when making decisions.
Uses the raw REST API so we don't require the full alpaca-py surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import requests


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    side: str  # "long" or "short"
    market_value: float
    unrealized_pl: float


@dataclass
class AccountSnapshot:
    equity_usd: float
    cash_usd: float
    buying_power_usd: float
    positions: List[Position]


class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str,
                 base_url: str = "https://paper-api.alpaca.markets"):
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

    def _get(self, path: str) -> Dict:
        r = requests.get(f"{self.base_url}{path}", headers=self._headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def snapshot(self) -> AccountSnapshot:
        acct = self._get("/v2/account")
        raw_positions = self._get("/v2/positions")
        positions = [
            Position(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                side=p["side"],
                market_value=float(p["market_value"]),
                unrealized_pl=float(p["unrealized_pl"]),
            )
            for p in raw_positions
        ]
        return AccountSnapshot(
            equity_usd=float(acct["equity"]),
            cash_usd=float(acct["cash"]),
            buying_power_usd=float(acct["buying_power"]),
            positions=positions,
        )

    def snapshot_dict(self) -> Dict:
        """JSON-serializable snapshot for history logging."""
        s = self.snapshot()
        return {
            "equity_usd": s.equity_usd,
            "cash_usd": s.cash_usd,
            "buying_power_usd": s.buying_power_usd,
            "positions": [
                {"symbol": p.symbol, "qty": p.qty, "side": p.side,
                 "avg_entry_price": p.avg_entry_price,
                 "market_value": p.market_value,
                 "unrealized_pl": p.unrealized_pl}
                for p in s.positions
            ],
        }
