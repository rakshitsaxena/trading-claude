"""Run pessimistic/conservative/optimistic social-sentiment scenarios.

Prints a matrix of direction/size/reason per symbol per profile so we can
assess agreement across hurdle settings before taking a call.
"""
from __future__ import annotations

import pandas as pd

from src.strategies.base import DecisionContext
from src.strategies.social_sentiment import SocialSentiment


PROFILES = {
    "pessimistic":  dict(min_messages=25, min_sigma=1.5, fallback_edge=0.25, size=0.5),
    "conservative": dict(min_messages=10, min_sigma=1.0, fallback_edge=0.15, size=1.0),
    "optimistic":   dict(min_messages=5,  min_sigma=0.5, fallback_edge=0.05, size=1.0),
}

SYMBOLS = ["GME", "TSLA", "PLTR", "AMC", "NVDA", "AMD", "COIN", "RIVN"]


def run() -> dict:
    out: dict = {}
    for sym in SYMBOLS:
        out[sym] = {}
        for name, params in PROFILES.items():
            s = SocialSentiment(read_only=True, **params)
            ctx = DecisionContext(
                as_of=pd.Timestamp.now(tz="US/Eastern"),
                symbol=sym,
                open_window=pd.DataFrame(),
                daily_history=pd.DataFrame(),
            )
            sig = s.decide(ctx)
            out[sym][name] = {
                "direction": sig.direction,
                "size": round(sig.size, 3),
                "reason": sig.reason,
            }
    return out


if __name__ == "__main__":
    import json
    results = run()
    for sym, by_profile in results.items():
        print(f"\n=== {sym} ===")
        for prof, r in by_profile.items():
            arrow = "↑" if r["direction"] > 0 else ("↓" if r["direction"] < 0 else "·")
            print(f"  {prof:13s} {arrow} dir={r['direction']:+d} size={r['size']:.2f}  {r['reason']}")
    print("\n--- JSON ---")
    print(json.dumps(results, indent=2))
