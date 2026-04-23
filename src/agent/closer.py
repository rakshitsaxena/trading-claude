"""Close-slot flattener. NO Claude call.

At 15:00 ET: hit close_all_positions on Alpaca, snapshot realised P&L,
compute SPY intraday return for alpha attribution, log a close row.
The daily brief (separate module) consumes this row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml

from ..broker.alpaca_client import AlpacaClient
from ..data.bars import BarRequest, fetch, group_by_session, ET
from ..history import store as history_store
from ..notify.telegram import Telegram


ROOT = Path(__file__).resolve().parents[2]


def _load_config() -> Dict:
    path = ROOT / "config.yaml"
    if not path.exists():
        path = ROOT / "config.example.yaml"
    return yaml.safe_load(path.read_text())


def _last_open_row() -> Optional[Dict]:
    rows = history_store.read_window(days=3)
    opens = [r for r in rows if r.get("slot") == "open" and r.get("basket")]
    return opens[-1] if opens else None


def _spy_intraday_pct() -> Optional[float]:
    """SPY return from 10:00 → 15:00 today (for alpha attribution)."""
    try:
        m30 = fetch(BarRequest("SPY", "30m", 5))
    except Exception:
        return None
    today = datetime.now(tz=pd.Timestamp.now(tz=ET).tz).date()
    for d, sess in group_by_session(m30):
        if d != today:
            continue
        entry_rows = sess[sess.index.time < pd.Timestamp("10:00").time()]
        exit_rows = sess[sess.index.time == pd.Timestamp("14:30").time()]
        if entry_rows.empty or exit_rows.empty:
            return None
        e = float(entry_rows.iloc[-1]["close"])
        x = float(exit_rows.iloc[0]["close"])
        return x / e - 1
    return None


@dataclass
class CloseResult:
    orders: List[Dict]
    realised_pnl_pct: Optional[float]
    spy_intraday_pct: Optional[float]
    alpha_pct: Optional[float]
    history_path: Path


def run(*, dry_run: bool = False) -> CloseResult:
    cfg = _load_config()

    alpaca: Optional[AlpacaClient] = None
    snapshot_before = None
    if cfg.get("alpaca", {}).get("api_key", "").startswith("PK"):
        alpaca = AlpacaClient(
            api_key=cfg["alpaca"]["api_key"],
            api_secret=cfg["alpaca"]["api_secret"],
            base_url=cfg["alpaca"].get("base_url", "https://paper-api.alpaca.markets"),
        )
        try:
            snapshot_before = alpaca.snapshot_dict()
        except Exception as e:
            snapshot_before = {"error": str(e)}

    orders: List[Dict] = []
    if alpaca and not dry_run:
        try:
            raw = alpaca.close_all_positions()
            # Alpaca returns per-symbol results
            if isinstance(raw, list):
                for item in raw:
                    orders.append({
                        "symbol": item.get("symbol"),
                        "status": item.get("status"),
                        "body": item.get("body"),
                    })
        except Exception as e:
            orders.append({"error": str(e)})

    # Snapshot after + realised PnL
    snapshot_after = None
    realised_pnl_pct: Optional[float] = None
    if alpaca:
        try:
            snapshot_after = alpaca.snapshot_dict()
            if (snapshot_before and snapshot_after
                    and "equity_usd" in snapshot_before
                    and "equity_usd" in snapshot_after):
                e0 = float(snapshot_before["equity_usd"])
                e1 = float(snapshot_after["equity_usd"])
                if e0 > 0:
                    realised_pnl_pct = e1 / e0 - 1
        except Exception as e:
            snapshot_after = {"error": str(e)}

    spy_intraday_pct = _spy_intraday_pct()
    alpha_pct = None
    if realised_pnl_pct is not None and spy_intraday_pct is not None:
        alpha_pct = realised_pnl_pct - spy_intraday_pct

    # Log row
    last_open = _last_open_row()
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "slot": "close",
        "orders": orders,
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "realised_pnl_pct": realised_pnl_pct,
        "spy_intraday_pct": spy_intraday_pct,
        "alpha_pct": alpha_pct,
        "paired_open_ts": (last_open or {}).get("ts"),
        "basket": (last_open or {}).get("basket", []),
        "regime": (last_open or {}).get("regime"),
    }
    path = history_store.append(row)

    # Telegram
    tg_cfg = cfg.get("telegram", {})
    if tg_cfg.get("bot_token") and tg_cfg.get("chat_id"):
        try:
            tg = Telegram(tg_cfg["bot_token"], str(tg_cfg["chat_id"]))
            tg.send(_format_close(realised_pnl_pct, spy_intraday_pct, alpha_pct, orders))
        except Exception as e:
            print(f"[telegram] send failed: {e}")

    return CloseResult(
        orders=orders, realised_pnl_pct=realised_pnl_pct,
        spy_intraday_pct=spy_intraday_pct, alpha_pct=alpha_pct, history_path=path,
    )


def _format_close(pnl, spy, alpha, orders) -> str:
    def pct(x): return f"{x*100:+.2f}%" if x is not None else "n/a"
    lines = ["*15:00 ET* — Flatten"]
    lines.append(f"Closed {len(orders)} position(s)")
    lines.append(f"PnL: {pct(pnl)}  |  SPY intra: {pct(spy)}  |  Alpha: {pct(alpha)}")
    return "\n".join(lines)
