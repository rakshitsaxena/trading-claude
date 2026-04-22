"""JSONL history store. One row per agent invocation.

Schema (extend freely; unknown fields ignored on read):
  {
    "ts": "2026-04-22T14:00:00+00:00",   # ISO UTC when decision was made
    "slot": "open" | "close",
    "as_of_et": "2026-04-22T10:00:00-04:00",
    "symbol": "SPY",
    "action": "BUY" | "SELL" | "HOLD" | "FLAT" | "CLOSE",
    "direction": 1 | -1 | 0,
    "size_gbp": 0,
    "shares": 0,
    "entry_hint_price": 450.12,
    "strategy_signals": [ { "name": "orb", "direction": 1, "reason": "..." } ],
    "rationale": "free-text Claude explanation",
    "confidence": 0.72,
    "realized_pnl_pct": null,   # filled on the matching close row if we can
    "alpaca_snapshot": { "equity_usd": 100000, "positions": [...] }
  }

Files are partitioned by month: history/2026-04.jsonl
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

HIST_DIR = Path(__file__).resolve().parents[2] / "history"
HIST_DIR.mkdir(exist_ok=True)


def _month_file(d: date) -> Path:
    return HIST_DIR / f"{d.strftime('%Y-%m')}.jsonl"


def append(row: Dict) -> Path:
    """Append a row to this month's JSONL. Returns the file path."""
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    try:
        when = datetime.fromisoformat(row["ts"])
    except (TypeError, ValueError):
        when = datetime.now(timezone.utc)
    path = _month_file(when.date())
    with path.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return path


def read_window(days: int = 30) -> List[Dict]:
    """Read all rows from the last `days` days across monthly files, ordered oldest-first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: List[Dict] = []
    files = sorted(HIST_DIR.glob("*.jsonl"))
    for fp in files:
        with fp.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    ts = datetime.fromisoformat(row["ts"])
                except Exception:
                    continue
                if ts >= cutoff:
                    rows.append(row)
    rows.sort(key=lambda r: r["ts"])
    return rows


def summarize(rows: Iterable[Dict]) -> Dict:
    """Compact stats over a row window — useful for Claude context."""
    rows = list(rows)
    n = len(rows)
    n_trades = sum(1 for r in rows if r.get("action") in ("BUY", "SELL"))
    realized = [r["realized_pnl_pct"] for r in rows
                if isinstance(r.get("realized_pnl_pct"), (int, float))]
    n_wins = sum(1 for p in realized if p > 0)
    total_ret = 1.0
    for p in realized:
        total_ret *= (1 + p)
    total_ret -= 1
    return {
        "rows": n,
        "trades": n_trades,
        "completed_trades": len(realized),
        "hit_rate": (n_wins / len(realized)) if realized else None,
        "total_return_pct": total_ret if realized else None,
    }


def last_open_without_close(symbol: Optional[str] = None) -> Optional[Dict]:
    """Find most recent 'open' slot row that doesn't yet have a matching close.
    Used so the close slot can pair PnL to its open."""
    rows = read_window(days=2)
    opens = [r for r in rows if r.get("slot") == "open"
             and r.get("direction", 0) != 0
             and (symbol is None or r.get("symbol") == symbol)]
    closes = [r for r in rows if r.get("slot") == "close"]
    if not opens:
        return None
    last = opens[-1]
    last_ts = last["ts"]
    for c in closes:
        if c["ts"] > last_ts:
            return None
    return last
