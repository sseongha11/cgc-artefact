"""Red-team injection for the CGC runtime.

Deterministically decides, per case, whether (and how) to poison an agent so
blast-radius is reproducible. Dosage = fraction of cases with >=1 poisoned agent.
"""
from __future__ import annotations

import hashlib

MODES = ["prompt-poison", "sycophancy", "confident-wrong", "collusion"]
AGENTS = ["Org1MSP", "Org2MSP", "Org3MSP"]


def _h(*parts) -> float:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def poison_for(case_id: str, rate: float, mode: str, seed: str = "rt") -> dict:
    """Return {org: mode} for this case, or {} if not selected. `collusion` poisons
    two agents (breaks 2-of-3); other modes poison exactly one."""
    if rate <= 0 or mode == "none":
        return {}
    if _h(seed, case_id, "select") >= rate:
        return {}
    # pick target org(s) deterministically
    idx = int(_h(seed, case_id, "org") * len(AGENTS)) % len(AGENTS)
    if mode == "collusion":
        a, b = AGENTS[idx], AGENTS[(idx + 1) % len(AGENTS)]
        return {a: "confident-wrong", b: "confident-wrong"}
    return {AGENTS[idx]: mode}


def attack_for(case_id: str, rate: float, seed: str = "rt") -> bool:
    """Whether the compromised operator attacks this case (independent of poison)."""
    return rate > 0 and _h(seed, case_id, "attack") < rate
