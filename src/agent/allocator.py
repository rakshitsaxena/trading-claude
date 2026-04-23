"""Open-slot portfolio allocator. One Claude call per day.

Flow:
  1. Load config + Alpaca snapshot + 30d history + last ~5 daily briefs.
  2. Build mechanical candidates (gap_reversion today; pair_divergence later).
  3. Build regime snapshot (SPY daily + VIX).
  4. Halt check — if realised 5d return <= dd_halt_pct, force FLAT without calling Claude.
  5. Call Claude allocator → JSON basket proposal.
  6. Clamp basket to code-enforced risk limits.
  7. Place notional orders on Alpaca paper.
  8. Append basket row to history; send Telegram.
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

from ..broker.alpaca_client import AlpacaClient
from ..data.bars import BarRequest, fetch, group_by_session, ET
from ..history import store as history_store
from ..notify.telegram import Telegram
from ..regime import classify as classify_regime
from ..signals.gap_reversion import gap_reversion_candidates
from .prompts import ALLOCATOR_SYSTEM, build_allocator_user_message


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
    raise RuntimeError("ANTHROPIC_API_KEY missing (env or .env)")


def _load_recent_briefs(n: int) -> List[str]:
    if not BRIEFS_PATH.exists():
        return []
    text = BRIEFS_PATH.read_text()
    # Briefs are separated by '---' lines.
    chunks = [c.strip() for c in text.split("\n---\n") if c.strip()]
    return chunks[-n:]


def _build_symbol_bundles(universe: List[str]) -> Dict[str, dict]:
    """Build the data bundle the gap-reversion signal expects."""
    bundles: Dict[str, dict] = {}
    for sym in universe:
        try:
            m30 = fetch(BarRequest(sym, "30m", 60))
            daily = fetch(BarRequest(sym, "1d", 400))
        except Exception as e:
            print(f"[warn] {sym} bar fetch failed: {e}")
            continue
        if m30.empty or daily.empty:
            continue
        opens_by_date = {}
        for d, session in group_by_session(m30):
            open_bar = session[session.index.time == pd.Timestamp("09:30").time()]
            if not open_bar.empty:
                opens_by_date[d] = float(open_bar.iloc[0]["open"])
        bundles[sym] = {"daily": daily, "opens_by_date": opens_by_date}
    return bundles


def _realised_5d(history_rows: List[dict]) -> Optional[float]:
    basket_rows = [r for r in history_rows
                   if r.get("slot") == "close"
                   and isinstance(r.get("realised_pnl_pct"), (int, float))]
    if not basket_rows:
        return None
    last5 = basket_rows[-5:]
    cum = 1.0
    for r in last5:
        cum *= (1 + float(r["realised_pnl_pct"]))
    return cum - 1


def _clamp_basket(proposed: List[dict], limits: dict) -> List[dict]:
    """Enforce code-level risk caps on Claude's basket proposal."""
    max_pos = float(limits.get("max_position_pct", 0.25))
    max_gross = float(limits.get("max_gross_pct", 1.0))
    max_n = int(limits.get("max_positions", 4))
    min_conv = float(limits.get("min_conviction", 0.0))

    clean: List[dict] = []
    for p in proposed:
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol")
        side = p.get("side")
        weight = float(p.get("weight_pct", 0))
        if not sym or side not in ("long", "short") or weight <= 0:
            continue
        if p.get("conviction", 1.0) < min_conv:
            # This field is optional; if provided and below threshold, drop.
            continue
        weight = min(weight, max_pos)
        clean.append({**p, "weight_pct": weight})

    # Truncate to max_n, prioritising higher weight.
    clean.sort(key=lambda x: -x["weight_pct"])
    clean = clean[:max_n]

    # Scale down if gross exceeds cap.
    gross = sum(p["weight_pct"] for p in clean)
    if gross > max_gross and gross > 0:
        scale = max_gross / gross
        for p in clean:
            p["weight_pct"] = round(p["weight_pct"] * scale, 4)

    return clean


def _parse_json(text: str) -> Dict:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.M)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise


@dataclass
class AllocatorResult:
    decision: Dict
    orders_placed: List[Dict]
    history_path: Path
    halted: bool


def run(*, dry_run: bool = False) -> AllocatorResult:
    cfg = _load_config()
    universe = cfg["universe"]
    limits = cfg["portfolio"]
    signal_cfg = cfg.get("signals", {}).get("gap_reversion", {})
    dd_halt = float(cfg.get("dd_halt_pct", -0.03))
    history_days = int(cfg.get("history_window_days", 30))
    n_briefs = int(cfg.get("daily_briefs_to_include", 5))

    # --- Gather data (mechanical, no LLM) ---
    today = datetime.now(tz=pd.Timestamp.now(tz=ET).tz).date()
    bundles = _build_symbol_bundles(universe)
    if "SPY" not in bundles:
        raise RuntimeError("SPY bars unavailable — required for regime classification")

    candidates = gap_reversion_candidates(
        as_of_date=today,
        symbol_data=bundles,
        min_abs_z=float(signal_cfg.get("min_abs_z", 1.0)),
        lookback=int(signal_cfg.get("lookback_days", 30)),
    )
    try:
        vix = fetch(BarRequest("^VIX", "1d", 400))
    except Exception:
        vix = pd.DataFrame()
    regime = classify_regime(today, bundles["SPY"]["daily"], vix)

    # --- Account + history ---
    alpaca: Optional[AlpacaClient] = None
    account = None
    if cfg.get("alpaca", {}).get("api_key", "").startswith("PK"):
        alpaca = AlpacaClient(
            api_key=cfg["alpaca"]["api_key"],
            api_secret=cfg["alpaca"]["api_secret"],
            base_url=cfg["alpaca"].get("base_url", "https://paper-api.alpaca.markets"),
        )
        try:
            account = alpaca.snapshot_dict()
        except Exception as e:
            account = {"error": str(e)}

    recent_rows = history_store.read_window(days=history_days)
    hist_summary = history_store.summarize(recent_rows)
    realised_5d = _realised_5d(recent_rows)
    recent_briefs = _load_recent_briefs(n_briefs)

    # --- DD halt: skip Claude if recent drawdown too large ---
    halted = realised_5d is not None and realised_5d <= dd_halt
    if halted:
        decision = {
            "positions": [],
            "portfolio_rationale": (
                f"DD halt: 5d realised {realised_5d:+.2%} <= {dd_halt:+.2%}. "
                "Staying flat, no Claude call made."),
            "regime_read": regime.regime,
            "confidence": 0.0,
        }
        proposed: List[dict] = []
    elif dry_run or not cfg.get("anthropic", {}).get("enabled", True):
        decision = {
            "positions": [],
            "portfolio_rationale": "dry-run: no Claude call",
            "regime_read": regime.regime,
            "confidence": 0.0,
        }
        proposed = []
    else:
        user_msg = build_allocator_user_message(
            now_et=pd.Timestamp.now(tz=ET).isoformat(),
            candidates=[c.to_dict() for c in candidates],
            regime=regime.to_dict(),
            portfolio_limits=limits,
            account=account,
            history_summary=hist_summary,
            recent_trades=recent_rows,
            daily_briefs=recent_briefs,
            dd_halt_pct=dd_halt,
            realised_5d_pct=realised_5d,
        )
        client = Anthropic(api_key=_api_key())
        resp = client.messages.create(
            model=cfg.get("model", "claude-opus-4-7"),
            max_tokens=1500,
            system=ALLOCATOR_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", None) == "text")
        decision = _parse_json(text)
        proposed = decision.get("positions", [])

    # --- Code-enforced clamp ---
    clamped = _clamp_basket(proposed, limits)

    # --- Place orders ---
    orders: List[Dict] = []
    if clamped and alpaca and account and "equity_usd" in (account or {}):
        equity = float(account["equity_usd"])
        for p in clamped:
            notional = equity * float(p["weight_pct"])
            if notional < 1.0:
                continue
            side = "buy" if p["side"] == "long" else "sell"
            try:
                o = alpaca.place_notional_order(p["symbol"], side, notional)
                orders.append({"symbol": p["symbol"], "side": side,
                               "notional": notional, "order_id": o.get("id"),
                               "status": o.get("status")})
            except Exception as e:
                orders.append({"symbol": p["symbol"], "error": str(e)})

    # --- Persist ---
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "slot": "open",
        "basket": clamped,
        "proposed": proposed,
        "regime": regime.to_dict(),
        "candidates": [c.to_dict() for c in candidates],
        "decision": decision,
        "orders": orders,
        "account_before": account,
        "halted": halted,
    }
    path = history_store.append(row)

    # --- Telegram ---
    tg_cfg = cfg.get("telegram", {})
    if tg_cfg.get("bot_token") and tg_cfg.get("chat_id"):
        try:
            tg = Telegram(tg_cfg["bot_token"], str(tg_cfg["chat_id"]))
            tg.send(_format_open_message(clamped, regime, halted, decision))
        except Exception as e:
            print(f"[telegram] send failed: {e}")

    return AllocatorResult(
        decision=decision, orders_placed=orders, history_path=path, halted=halted,
    )


def _format_open_message(basket, regime, halted, decision) -> str:
    if halted:
        return f"*10:00 ET* — HALTED (5d DD)\nRegime: {regime.regime}"
    if not basket:
        return (f"*10:00 ET* — FLAT\nRegime: {regime.regime}\n"
                f"_{decision.get('portfolio_rationale','')}_")
    lines = [f"*10:00 ET* — Basket (regime: {regime.regime})"]
    for p in basket:
        sign = "+" if p["side"] == "long" else "-"
        lines.append(f"  {sign}{p['symbol']}  {p['weight_pct']*100:.1f}%  "
                     f"_{p.get('reason','')}_")
    lines.append(f"\n_{decision.get('portfolio_rationale','')}_")
    return "\n".join(lines)
