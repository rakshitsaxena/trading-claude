"""StockTwits sentiment with a rolling per-symbol baseline.

Why baseline: StockTwits is structurally bullish (~60% bull-share is typical
for most tickers), so an absolute threshold overfires long. Instead we compare
today's bull_share to a rolling mean + std of that symbol's own history and
only fire on meaningful deviations.

Flow at 10:00 ET:
  1. Skip symbols in DENY (ETFs / mega-caps: retail chatter too dilute).
  2. Pull ~30 most-recent messages; count Bullish / Bearish tags.
  3. Load this symbol's history of bull_share observations.
  4. If history thin (< MIN_HISTORY), fall back to absolute 50%±edge threshold.
     Else z-score today against the rolling window; fire beyond ±MIN_SIGMA.
  5. Append today's observation to the history file.

Persistence: history/social_sentiment.jsonl (one obs per symbol per day).
Public StockTwits endpoint, no auth; ~200 req/hr soft limit.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import requests

from .base import Strategy, Signal, DecisionContext, FLAT


STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
STATE_FILE = Path(__file__).resolve().parents[2] / "history" / "social_sentiment.jsonl"

# Names where retail sentiment is too diluted to be informative.
DENY = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "AAPL", "MSFT", "GOOGL", "GOOG"}


class SocialSentiment(Strategy):
    name = "social_sentiment"

    def __init__(self, min_messages: int = 10, min_history: int = 10,
                 min_sigma: float = 1.0, fallback_edge: float = 0.15,
                 history_window: int = 60, size: float = 1.0,
                 timeout: float = 5.0, read_only: bool = False):
        self.min_messages = min_messages
        self.min_history = min_history
        self.min_sigma = min_sigma
        self.fallback_edge = fallback_edge
        self.history_window = history_window
        self.size = size
        self.timeout = timeout
        self.read_only = read_only  # skip state-file writes (backtests, scenario runs)

    # ----- I/O helpers (kept local so backtests can mock/skip) -----

    def _fetch(self, symbol: str) -> list[dict]:
        r = requests.get(
            STOCKTWITS_URL.format(symbol=symbol),
            timeout=self.timeout,
            headers={"User-Agent": "trading-claude/0.1"},
        )
        r.raise_for_status()
        return r.json().get("messages", [])

    def _load_history(self, symbol: str) -> List[float]:
        if not STATE_FILE.exists():
            return []
        out: List[float] = []
        with STATE_FILE.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("symbol") == symbol and isinstance(row.get("bull_share"), (int, float)):
                    out.append(float(row["bull_share"]))
        return out[-self.history_window:]

    def _append_history(self, symbol: str, bull_share: float, n_tagged: int) -> None:
        if self.read_only:
            return
        STATE_FILE.parent.mkdir(exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "bull_share": bull_share,
            "n_tagged": n_tagged,
        }
        with STATE_FILE.open("a") as f:
            f.write(json.dumps(row) + "\n")

    # ----- decision -----

    def decide(self, ctx: DecisionContext) -> Signal:
        if ctx.symbol in DENY:
            return Signal(0, 0.0, f"{ctx.symbol} on social denylist")

        try:
            messages = self._fetch(ctx.symbol)
        except Exception as e:
            return Signal(0, 0.0, f"stocktwits fetch failed: {e}")

        bulls = bears = 0
        for m in messages:
            sent = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
            if sent == "Bullish":
                bulls += 1
            elif sent == "Bearish":
                bears += 1

        tagged = bulls + bears
        if tagged < self.min_messages:
            return Signal(0, 0.0, f"only {tagged} tagged msgs (<{self.min_messages})")

        bull_share = bulls / tagged
        history = self._load_history(ctx.symbol)

        # Append *after* reading history so today's obs doesn't contaminate its own baseline.
        self._append_history(ctx.symbol, bull_share, tagged)

        if len(history) >= self.min_history:
            mean = sum(history) / len(history)
            var = sum((x - mean) ** 2 for x in history) / len(history)
            std = math.sqrt(var)
            if std < 1e-6:
                return Signal(0, 0.0, f"baseline std ~0 (n={len(history)})")
            z = (bull_share - mean) / std
            if abs(z) < self.min_sigma:
                return Signal(0, 0.0,
                              f"bull_share {bull_share:.0%} within baseline "
                              f"{mean:.0%}±{std:.0%} (z={z:+.2f})")
            direction = 1 if z > 0 else -1
            conviction = min(1.0, (abs(z) - self.min_sigma) / self.min_sigma + 0.25)
            sz = self.size * conviction
            side = "long" if direction > 0 else "short"
            return Signal(
                direction, sz,
                f"social {side} z={z:+.2f}: bull_share {bull_share:.0%} vs "
                f"baseline {mean:.0%}±{std:.0%} ({bulls}B/{bears}B, n={tagged})",
            )

        # Fallback: thin history, use absolute threshold as in v1.
        dev = bull_share - 0.5
        if abs(dev) < self.fallback_edge:
            return Signal(0, 0.0,
                          f"mixed sentiment (no baseline yet, hist={len(history)}): "
                          f"{bulls}B/{bears}B ({bull_share:.0%})")
        direction = 1 if dev > 0 else -1
        sz = self.size * 0.5  # half size until we have a baseline
        side = "long" if direction > 0 else "short"
        return Signal(
            direction, sz,
            f"social {side} (no baseline, hist={len(history)}): "
            f"{bulls}B/{bears}B ({bull_share:.0%}, n={tagged})",
        )
