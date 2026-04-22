"""Claude-powered decision agent.

Flow per invocation:
  1. Load config + history + Alpaca snapshot.
  2. For each universe symbol: fetch fresh 30m bars + daily, compute strategy signals.
  3. Build prompt, call Claude, parse JSON decision.
  4. Append history row. Send Telegram.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml
from anthropic import Anthropic

from .prompts import SYSTEM_PROMPT, build_user_message
from ..broker.alpaca_client import AlpacaClient
from ..data.bars import BarRequest, fetch, session_bars, ET
from ..history import store as history_store
from ..notify.telegram import Telegram, format_signal
from ..strategies.base import DecisionContext
from .. import strategies as strat_mod


ROOT = Path(__file__).resolve().parents[2]


def _load_config(fallback_example: bool = False) -> Dict:
    path = ROOT / "config.yaml"
    if not path.exists():
        if fallback_example and (ROOT / "config.example.yaml").exists():
            return yaml.safe_load((ROOT / "config.example.yaml").read_text())
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.yaml and fill in credentials.")
    return yaml.safe_load(path.read_text())


def _signals_for_symbol(symbol: str, strategy_names: List[str]) -> Dict:
    """Compute each named strategy's signal for today, at the current point in session."""
    m30 = fetch(BarRequest(symbol, "30m", days=30))
    daily = fetch(BarRequest(symbol, "1d", days=400))
    try:
        vix = fetch(BarRequest("^VIX", "1d", days=400))
    except Exception:
        vix = pd.DataFrame()

    today = datetime.now(tz=pd.Timestamp.now(tz=ET).tz).date()
    m30_today = session_bars(m30)
    m30_today = m30_today[m30_today.index.date == today]
    open_window = m30_today[m30_today.index.time < pd.Timestamp("10:00").time()]

    if open_window.empty or daily.empty:
        return {"_note": f"no usable open window yet for {symbol}", "signals": []}

    daily_hist = daily[daily.index.date < today]
    vix_hist = vix[vix.index.date < today] if not vix.empty else pd.DataFrame()

    ctx = DecisionContext(
        as_of=pd.Timestamp.combine(today, pd.Timestamp("10:00").time()).tz_localize(ET),
        symbol=symbol,
        open_window=open_window,
        daily_history=daily_hist,
        vix_history=vix_hist,
    )

    signals = []
    for name in strategy_names:
        try:
            sig = strat_mod.load(name).decide(ctx)
            signals.append({
                "name": name,
                "direction": sig.direction,
                "size": sig.size,
                "reason": sig.reason,
            })
        except Exception as e:
            signals.append({"name": name, "error": str(e)})

    entry_price = float(open_window.iloc[-1]["close"])
    return {
        "last_price": entry_price,
        "open_bar": {
            "open": float(open_window.iloc[0]["open"]),
            "high": float(open_window["high"].max()),
            "low": float(open_window["low"].min()),
            "close": entry_price,
            "volume": float(open_window["volume"].sum()),
        },
        "signals": signals,
    }


def _parse_claude_json(text: str) -> Dict:
    """Extract the first JSON object from Claude's response."""
    # Strip code fences if present
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.M)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find first {...} block
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"could not parse JSON from Claude response: {text[:500]}")


@dataclass
class AgentRunResult:
    decision: Dict
    history_path: Path


def run(slot: str, *, dry_run: bool = False) -> AgentRunResult:
    assert slot in ("open", "close"), f"invalid slot: {slot}"
    cfg = _load_config(fallback_example=dry_run)

    # --- Gather inputs
    universe = cfg["universe"]
    strategy_names = cfg.get("active_strategies", strat_mod.ALL)
    risk = cfg["risk"]

    signals_by_symbol = {sym: _signals_for_symbol(sym, strategy_names) for sym in universe}

    alpaca_snapshot = None
    if cfg.get("alpaca", {}).get("api_key"):
        try:
            client = AlpacaClient(
                api_key=cfg["alpaca"]["api_key"],
                api_secret=cfg["alpaca"]["api_secret"],
                base_url=cfg["alpaca"].get("base_url", "https://paper-api.alpaca.markets"),
            )
            alpaca_snapshot = client.snapshot_dict()
        except Exception as e:
            alpaca_snapshot = {"error": str(e)}

    history_window_days = cfg.get("history_window_days", 30)
    recent_rows = history_store.read_window(days=history_window_days)
    hist_summary = history_store.summarize(recent_rows)
    open_to_close = history_store.last_open_without_close() if slot == "close" else None

    now_et = pd.Timestamp.now(tz=ET).isoformat()

    # --- Call Claude
    user_msg = build_user_message(
        slot=slot, now_et=now_et, universe=universe,
        signals_by_symbol=signals_by_symbol,
        history_summary=hist_summary, recent_rows=recent_rows,
        account_snapshot=alpaca_snapshot, open_to_close=open_to_close,
        risk=risk,
    )

    model = cfg.get("model", "claude-opus-4-7")

    if dry_run:
        decision = {
            "symbol": universe[0],
            "action": "FLAT",
            "direction": 0,
            "size_gbp": 0,
            "shares": 0,
            "entry_hint_price": signals_by_symbol.get(universe[0], {}).get("last_price"),
            "confidence": 0.0,
            "rationale": "dry-run: no Claude call made",
        }
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            # try .env
            env_path = ROOT / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("ANTHROPIC_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        os.environ["ANTHROPIC_API_KEY"] = api_key
                        break
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY missing (env or .env)")
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        decision = _parse_claude_json(text)

    # --- Compute realized PnL on close if possible
    realized_pnl_pct = None
    if slot == "close" and open_to_close:
        entry = open_to_close.get("entry_hint_price")
        last_price = signals_by_symbol.get(open_to_close.get("symbol"), {}).get("last_price")
        direction = open_to_close.get("direction", 0)
        size = 1.0  # size is expressed in GBP; % return is independent of size
        if entry and last_price and direction:
            realized_pnl_pct = direction * (last_price / entry - 1) * size

    # --- Persist + notify
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "slot": slot,
        "as_of_et": now_et,
        "symbol": decision.get("symbol"),
        "action": decision.get("action"),
        "direction": decision.get("direction", 0),
        "size_gbp": decision.get("size_gbp", 0),
        "shares": decision.get("shares", 0),
        "entry_hint_price": decision.get("entry_hint_price"),
        "confidence": decision.get("confidence"),
        "rationale": decision.get("rationale"),
        "strategy_signals": signals_by_symbol.get(decision.get("symbol"), {}).get("signals", []),
        "alpaca_snapshot": alpaca_snapshot,
        "realized_pnl_pct": realized_pnl_pct,
    }
    path = history_store.append(row)

    # Telegram
    tg_cfg = cfg.get("telegram", {})
    if tg_cfg.get("bot_token") and tg_cfg.get("chat_id"):
        tg = Telegram(tg_cfg["bot_token"], str(tg_cfg["chat_id"]))
        msg = format_signal(
            slot=slot,
            symbol=decision.get("symbol") or "-",
            action=decision.get("action", "FLAT"),
            shares=decision.get("shares", 0),
            price_hint=decision.get("entry_hint_price") or 0,
            rationale=decision.get("rationale", ""),
            confidence=decision.get("confidence"),
        )
        if realized_pnl_pct is not None:
            msg += f"\n\n*Realized PnL:* `{realized_pnl_pct:+.2%}`"
        try:
            tg.send(msg)
        except Exception as e:
            # Log but don't crash — the history row is the source of truth
            print(f"[telegram] send failed: {e}")

    return AgentRunResult(decision=decision, history_path=path)
