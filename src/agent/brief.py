"""Daily brief. One small Claude call (Haiku-tier), appended to
history/daily_briefs.md. The allocator reads the last N briefs on the next run.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from anthropic import Anthropic

from ..history import store as history_store
from .prompts import BRIEF_SYSTEM, build_brief_user_message


ROOT = Path(__file__).resolve().parents[2]
BRIEFS_PATH = ROOT / "history" / "daily_briefs.md"


def _load_config() -> Dict:
    path = ROOT / "config.yaml"
    if not path.exists():
        path = ROOT / "config.example.yaml"
    return yaml.safe_load(path.read_text())


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip()
                os.environ["ANTHROPIC_API_KEY"] = key
                return key
    raise RuntimeError("ANTHROPIC_API_KEY missing")


def _last_close_row() -> Optional[Dict]:
    rows = history_store.read_window(days=2)
    closes = [r for r in rows if r.get("slot") == "close"]
    return closes[-1] if closes else None


def _append_brief(date_str: str, text: str) -> None:
    BRIEFS_PATH.parent.mkdir(exist_ok=True)
    with BRIEFS_PATH.open("a") as f:
        if BRIEFS_PATH.stat().st_size > 0:
            f.write("\n---\n")
        f.write(text.strip() + "\n")


def run(*, dry_run: bool = False) -> Dict:
    cfg = _load_config()
    close_row = _last_close_row()
    if not close_row:
        return {"skipped": True, "reason": "no close row yet today"}

    date_str = datetime.now().date().isoformat()

    if dry_run:
        note = f"{date_str}: dry-run brief (no Claude)."
        _append_brief(date_str, note)
        return {"brief": note, "dry_run": True}

    user_msg = build_brief_user_message(
        date_str=date_str,
        regime=close_row.get("regime") or {},
        positions_taken=close_row.get("basket") or [],
        realised_pnl_pct=close_row.get("realised_pnl_pct") or 0.0,
        spy_intraday_pct=close_row.get("spy_intraday_pct") or 0.0,
        alpha_pct=close_row.get("alpha_pct") or 0.0,
    )

    client = Anthropic(api_key=_api_key())
    resp = client.messages.create(
        model=cfg.get("model_brief", "claude-haiku-4-5-20251001"),
        max_tokens=400,
        system=BRIEF_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in resp.content
                   if getattr(b, "type", None) == "text").strip()
    _append_brief(date_str, text)
    return {"brief": text}
