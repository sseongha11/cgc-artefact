"""LangGraph orchestration of the CGC consensus-gated workflow.

Graph shape (one 'decide' step):

    START ─┬─▶ review:Org1 ─┐
           ├─▶ review:Org2 ─┼─▶ propose ─▶ vote ─▶ finalize ─▶ (COMMITTED/exhausted) ─▶ END
           └─▶ review:Org3 ─┘                          │
                                                        └─(REJECTED & rounds<max)─▶ repropose ─▶ vote …

Each review node is one agent independently judging the case (parallel fan-out).
propose/vote/finalize call the ledger (LocalLedger or ApiLedger) so the endorsement
gate — not this script — decides whether the action commits.
"""
from __future__ import annotations

import time
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import START, END, StateGraph

from reviewer import APPROVE, REJECT
from ledger import COMMITTED, REJECTED, OPEN, AttackRejected

AGENTS = ["Org1MSP", "Org2MSP", "Org3MSP"]
PROPOSER = "Org1MSP"
STEP = "decide"


def _merge(a: dict, b: dict) -> dict:
    out = dict(a or {})
    out.update(b or {})
    return out


class State(TypedDict, total=False):
    case: dict
    poison: dict                       # org -> injection mode
    reviewer: Any
    ledger: Any
    max_rounds: int
    verdicts: Annotated[dict, _merge]  # org -> {"decision","reason","poisoned"}
    proposal_action: str
    status: str
    rounds: int
    committed: Optional[str]
    attack: bool                       # the coordinator's operator attacks this case
    attack_mode: str                   # "forge" (before voting) or "omit" (after)
    attack_attempted: bool
    attack_succeeded: bool


def _review_node(org: str):
    def node(state: State) -> dict:
        poison = (state.get("poison") or {}).get(org)
        v = state["reviewer"].review(state["case"], org, poison)
        return {"verdicts": {org: v}}
    return node


def _proposer_action(state: State) -> str:
    v = state["verdicts"][PROPOSER]["decision"]
    return v if v in (APPROVE, REJECT) else APPROVE  # sycophant-proposer fallback


def _propose(state: State) -> dict:
    case_id = state["case"]["case_id"]
    action = _proposer_action(state)
    state["ledger"].propose_step(PROPOSER, case_id, STEP, action, state["case"].get("payload_hash", ""))
    return {"proposal_action": action, "rounds": 1}


def _forge(state: State, action: str) -> dict:
    """Compromised operator (the proposer's org) front runs a dissenting victim by
    casting an endorsement in the victim's name before the victim votes."""
    victim = next((o for o in AGENTS if o != PROPOSER
                   and state["verdicts"][o]["decision"] not in (action, "DEFER")), None)
    if victim is None:
        return {"attack_attempted": False, "attack_succeeded": False}  # nothing to overturn
    try:
        state["ledger"].forge_vote(PROPOSER, victim, state["case"]["case_id"], STEP, True, "forged")
        return {"attack_attempted": True, "attack_succeeded": True}
    except AttackRejected:
        return {"attack_attempted": True, "attack_succeeded": False}


OPERATOR = "Org2MSP"  # runs the coordinator in the omission attack; not the proposer


def _omit(state: State, action: str) -> dict:
    """Compromised operator silently drops another organisation's endorsement,
    to block a proposal it does not want to pass."""
    p = state["ledger"].get_proposal(state["case"]["case_id"], STEP)
    votes = p.votes if hasattr(p, "votes") else p.get("votes", {})
    victim = next((o for o in AGENTS if o != OPERATOR and o in votes and votes[o]["endorse"]), None)
    if victim is None:
        return {"attack_attempted": False, "attack_succeeded": False}  # nothing to drop
    try:
        state["ledger"].drop_vote(OPERATOR, victim, state["case"]["case_id"], STEP)
        return {"attack_attempted": True, "attack_succeeded": True}
    except AttackRejected:
        return {"attack_attempted": True, "attack_succeeded": False}


def _vote(state: State) -> dict:
    case_id = state["case"]["case_id"]
    action = state["proposal_action"]
    led = state["ledger"]
    out = {}
    first = state.get("attack") and state.get("rounds", 1) == 1
    if first and state.get("attack_mode", "forge") == "forge":
        out = _forge(state, action)
    p = led.get_proposal(case_id, STEP)
    already = set(p.votes.keys()) if hasattr(p, "votes") else set(p.get("votes", {}))
    for org in AGENTS:
        if org in already:
            continue
        d = state["verdicts"][org]["decision"]
        endorse = True if d == "DEFER" else (d == action)  # sycophant endorses proposer
        led.vote_step(org, case_id, STEP, endorse, state["verdicts"][org]["reason"])
    if first and state.get("attack_mode") == "omit":
        out = _omit(state, action)
    return out


def _finalize(state: State) -> dict:
    case_id = state["case"]["case_id"]
    status = state["ledger"].finalize_step(case_id, STEP)
    committed = None
    if status == COMMITTED:
        p = state["ledger"].get_proposal(case_id, STEP)
        committed = p.outcome if hasattr(p, "outcome") else p.get("outcome")
    return {"status": status, "committed": committed}


def _repropose(state: State) -> dict:
    """Flip to the alternative action and try again (conflict-resolution cost)."""
    case_id = state["case"]["case_id"]
    other = REJECT if state["proposal_action"] == APPROVE else APPROVE
    state["ledger"].repropose_step(PROPOSER, case_id, STEP, other)
    return {"proposal_action": other, "rounds": state.get("rounds", 1) + 1}


def _route(state: State) -> str:
    if state["status"] == COMMITTED:
        return "end"
    if state["status"] in (REJECTED, OPEN) and state.get("rounds", 1) < state.get("max_rounds", 1):
        return "repropose"
    return "end"


def build_workflow():
    g = StateGraph(State)
    for org in AGENTS:
        g.add_node(f"review_{org}", _review_node(org))
        g.add_edge(START, f"review_{org}")
        g.add_edge(f"review_{org}", "propose")
    g.add_node("propose", _propose)
    g.add_node("vote", _vote)
    g.add_node("finalize", _finalize)
    g.add_node("repropose", _repropose)
    g.add_edge("propose", "vote")
    g.add_edge("vote", "finalize")
    g.add_conditional_edges("finalize", _route, {"repropose": "repropose", "end": END})
    g.add_edge("repropose", "vote")
    return g.compile()


def run_case(app, case: dict, reviewer, ledger, poison=None, max_rounds=1, attack=False,
             attack_mode: str = "forge") -> dict:
    """Run one case through the gated workflow; return a metrics-ready result."""
    tx0 = getattr(ledger, "tx_count", 0)
    t0 = time.perf_counter()
    final = app.invoke({
        "case": case, "poison": poison or {}, "reviewer": reviewer,
        "ledger": ledger, "max_rounds": max_rounds, "verdicts": {}, "attack": attack,
        "attack_mode": attack_mode,
    })
    dt = (time.perf_counter() - t0) * 1000
    truth = case["label"]
    committed = final.get("committed")
    return {
        "case_id": case["case_id"],
        "ground_truth": truth,
        "status": final["status"],
        "committed": committed,
        "correct": committed == truth,           # correct only if the RIGHT action committed
        "wrong_commit": committed is not None and committed != truth,  # blast: wrong action committed
        "rounds": final.get("rounds", 1),
        "verdicts": {o: v["decision"] for o, v in final["verdicts"].items()},
        "poisoned_orgs": [o for o in (poison or {})],
        "latency_ms": round(dt, 2),
        "tx_writes": getattr(ledger, "tx_count", 0) - tx0,
        "models": {o: v.get("model", "mock") for o, v in final["verdicts"].items()},
        "parse_failures": sum(not v.get("parse_ok", True) for v in final["verdicts"].values()),
        # Tokens and cost are those of the original call, even when served from cache.
        "tokens_in": sum(v.get("tokens_in", 0) for v in final["verdicts"].values()),
        "tokens_out": sum(v.get("tokens_out", 0) for v in final["verdicts"].values()),
        "tokens_reasoning": sum(v.get("tokens_reasoning", 0) for v in final["verdicts"].values()),
        "cost_usd": sum(v.get("cost_usd", 0.0) for v in final["verdicts"].values()),
        "attack_attempted": final.get("attack_attempted", False),
        "attack_succeeded": final.get("attack_succeeded", False),
    }
