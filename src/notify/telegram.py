"""Minimal Telegram sender. No bot library; just the HTTP API."""
from __future__ import annotations

import requests


class Telegram:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, text: str, parse_mode: str = "Markdown") -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()


def format_signal(slot: str, symbol: str, action: str, shares: float,
                  price_hint: float, rationale: str,
                  confidence: float | None = None) -> str:
    """Pretty Telegram message for a trading signal."""
    header = "MARKET OPEN" if slot == "open" else "MARKET CLOSE"
    lines = [
        f"*{header} — {symbol}*",
        f"*Action:* `{action}`",
    ]
    if shares:
        lines.append(f"*Shares:* `{shares:g}` at ~`${price_hint:.2f}`")
    if confidence is not None:
        lines.append(f"*Confidence:* {confidence:.0%}")
    lines.append("")
    lines.append("*Why:*")
    lines.append(rationale.strip())
    return "\n".join(lines)
