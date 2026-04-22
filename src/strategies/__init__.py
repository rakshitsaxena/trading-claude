from .base import Strategy, Signal, DecisionContext, FLAT
from .orb import ORB


def load(name: str) -> Strategy:
    name = name.lower()
    if name == "orb":
        return ORB()
    if name == "gap_fade":
        from .gap_fade import GapFade
        return GapFade()
    if name == "overnight_momentum":
        from .overnight_momentum import OvernightMomentum
        return OvernightMomentum()
    if name == "vwap_reversion":
        from .vwap_reversion import VWAPReversion
        return VWAPReversion()
    if name == "ensemble":
        from .ensemble import Ensemble
        return Ensemble()
    raise ValueError(f"unknown strategy: {name}")


ALL = ["orb", "gap_fade", "overnight_momentum", "vwap_reversion", "ensemble"]
