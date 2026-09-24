"""Coordinator semantics: same gate, different forgery resistance.

Run from agents/:  ../.venv/bin/python -m pytest -q tests
"""
import pytest

from coordinators import LedgerlessVoting, TrustedOrchestrator
from graph import build_workflow, run_case
from ledger import ANY, MAJORITY, ROLE_WEIGHTED, UNANIMOUS, ForgeryRejected, LocalLedger
from reviewer import APPROVE, REJECT, MockReviewer

ORGS = ["Org1MSP", "Org2MSP", "Org3MSP"]


def _cases(n=40):
    return [{"case_id": f"C/{i}", "appeal_ref": f"C/{i}", "label": APPROVE if i % 2 else REJECT,
             "facts": {}} for i in range(n)]


def _open(led, case="C/0", action=APPROVE):
    led.set_policy(MAJORITY, 3)
    led.propose_step("Org1MSP", case, "decide", action)
    return case


@pytest.mark.parametrize("policy", [ANY, MAJORITY, UNANIMOUS, ROLE_WEIGHTED])
def test_same_decisions_without_attack(policy):
    """With honest operators the coordinators differ only in trust, not outcome."""
    app, rev = build_workflow(), MockReviewer(accuracy=0.7)
    runs = {}
    for name, led in [("cgc", LocalLedger()), ("to", TrustedOrchestrator()), ("lfv", LedgerlessVoting())]:
        led.set_policy(policy, 3, "Org3MSP", 2)
        runs[name] = [(r["committed"], r["rounds"]) for r in
                      (run_case(app, c, rev, led) for c in _cases())]
    assert runs["cgc"] == runs["to"] == runs["lfv"]


def test_orchestrator_forgery_overturns_dissent():
    led = TrustedOrchestrator()
    case = _open(led)
    led.forge_vote("Org1MSP", "Org2MSP", case, "decide", True)
    led.vote_step("Org1MSP", case, "decide", True)
    assert led.finalize_step(case, "decide") == "COMMITTED"   # Org3 never needed


def test_ledgerless_rejects_forgery_and_keeps_honest_vote():
    led = LedgerlessVoting()
    case = _open(led)
    led.vote_step("Org2MSP", case, "decide", False, "dissent")
    with pytest.raises(ForgeryRejected):
        led.forge_vote("Org1MSP", "Org2MSP", case, "decide", True)
    assert led.get_proposal(case, "decide").votes["Org2MSP"]["endorse"] is False


def test_ledgerless_rejects_tampered_signature():
    led = LedgerlessVoting()
    case = _open(led)
    sig = led._keys["Org2MSP"].sign(led._message("Org2MSP", case, "decide", False))
    with pytest.raises(ForgeryRejected):  # signed "reject", presented as "endorse"
        led._accept("Org2MSP", case, "decide", True, "", sig)


def test_consensus_gate_binds_votes_to_identity():
    led = LocalLedger()
    case = _open(led)
    with pytest.raises(ForgeryRejected):
        led.forge_vote("Org1MSP", "Org2MSP", case, "decide", True)


def test_baselines_write_nothing_to_a_ledger():
    for led in (TrustedOrchestrator(), LedgerlessVoting()):
        case = _open(led)
        for o in ORGS:
            led.vote_step(o, case, "decide", True)
        led.finalize_step(case, "decide")
        assert led.tx_count == 0
    led = LocalLedger()
    case = _open(led)
    for o in ORGS:
        led.vote_step(o, case, "decide", True)
    led.finalize_step(case, "decide")
    assert led.tx_count == 6   # policy + propose + 3 votes + finalize


def test_attack_through_workflow():
    """End to end: only the trusted orchestrator lets a forgery through."""
    app, rev = build_workflow(), MockReviewer(accuracy=0.6)
    for led, expect in [(TrustedOrchestrator(), True), (LedgerlessVoting(), False), (LocalLedger(), False)]:
        led.set_policy(MAJORITY, 3)
        res = [run_case(app, c, rev, led, attack=True) for c in _cases()]
        tried = [r for r in res if r["attack_attempted"]]
        assert tried, "no case had a dissenting victim"
        assert all(r["attack_succeeded"] is expect for r in tried)


def test_omission_passes_signatures_but_not_the_ledger():
    """Signatures stop forgery but not a tally that drops a vote; the ledger stops both."""
    from ledger import AttackRejected
    for led, expect in [(TrustedOrchestrator(), True), (LedgerlessVoting(), True), (LocalLedger(), False)]:
        case = _open(led)
        for o in ORGS:
            led.vote_step(o, case, "decide", True)
        if expect:
            led.drop_vote("Org2MSP", "Org3MSP", case, "decide")
            assert "Org3MSP" not in led.get_proposal(case, "decide").votes
        else:
            with pytest.raises(AttackRejected):
                led.drop_vote("Org2MSP", "Org3MSP", case, "decide")


def test_omission_through_workflow_blocks_decisions_only_off_ledger():
    app, rev = build_workflow(), MockReviewer(accuracy=0.8)
    blocked = {}
    for name, led in [("to", TrustedOrchestrator()), ("lfv", LedgerlessVoting()), ("cgc", LocalLedger())]:
        led.set_policy(UNANIMOUS, 3)
        res = [run_case(app, c, rev, led, attack=True, attack_mode="omit") for c in _cases()]
        blocked[name] = sum(r["attack_succeeded"] for r in res)
    assert blocked["to"] > 0 and blocked["lfv"] == blocked["to"] and blocked["cgc"] == 0
