"""Alpaca paper client.

Read (account/positions) and write (market orders, close-all) against the paper
endpoint. Raw REST so we don't carry the full alpaca-py surface. Paper only —
`base_url` is checked to contain 'paper' before any order placement.
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

    def _post(self, path: str, body: Dict) -> Dict:
        r = requests.post(f"{self.base_url}{path}", headers=self._headers,
                          json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = requests.delete(f"{self.base_url}{path}", headers=self._headers, timeout=10)
        r.raise_for_status()

    def _paper_guard(self) -> None:
        if "paper" not in self.base_url:
            raise RuntimeError(
                f"refusing to place order on non-paper URL: {self.base_url}"
            )

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

    def place_notional_order(self, symbol: str, side: str, notional_usd: float,
                             tif: str = "day") -> Dict:
        """Market order sized by USD notional (fractional shares OK on Alpaca)."""
        self._paper_guard()
        if side not in ("buy", "sell"):
            raise ValueError(f"invalid side: {side}")
        if notional_usd <= 0:
            raise ValueError(f"notional must be positive: {notional_usd}")
        body = {
            "symbol": symbol,
            "notional": f"{notional_usd:.2f}",
            "side": side,
            "type": "market",
            "time_in_force": tif,
        }
        return self._post("/v2/orders", body)

    def close_all_positions(self, cancel_orders: bool = True) -> List[Dict]:
        """Liquidate all open positions at market. Returns list of order responses."""
        self._paper_guard()
        path = "/v2/positions"
        if cancel_orders:
            path += "?cancel_orders=true"
        r = requests.delete(f"{self.base_url}{path}", headers=self._headers, timeout=20)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return []

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
