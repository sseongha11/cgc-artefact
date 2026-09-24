"""Baseline coordinators for RQ4: where the quorum decision is made.

All coordinators expose the LocalLedger interface, so graph.py and run.py are
unchanged, and all apply the same gate (`ledger.evaluate`). They differ only in
who holds the votes and whether a vote can be forged:

  TrustedOrchestrator  one organisation's coordinator tallies votes in memory.
                       Nothing is signed; the operator can overwrite any vote.
  LedgerlessVoting     same tally, but every vote carries an Ed25519 signature
                       from its organisation's key and is verified on receipt.
                       Forgery fails; omission and the audit log stay with the
                       operator.
  LocalLedger (CGC)    votes are bound to the caller's MSP identity by the
                       chaincode, so a forged vote is rejected (ledger.py).

The "compromised orchestrator" condition is not a separate class: it is an
attack run against TrustedOrchestrator. Two attacks are modelled, and running
them against every coordinator is what RQ4 compares:
  forge_vote  cast an endorsement in a dissenting organisation's name
              (stopped by signatures and by the ledger)
  drop_vote   silently discard another organisation's endorsement
              (not stopped by signatures: only the ledger keeps every vote)
"""
from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ledger import LocalLedger, ForgeryRejected


class _NoLedger(LocalLedger):
    """In-memory tally with no ledger: writes are not ledger transactions."""

    @property
    def tx_count(self) -> int:
        return 0

    @tx_count.setter
    def tx_count(self, _value) -> None:
        pass  # LocalLedger increments it; there is no ledger to write to


class TrustedOrchestrator(_NoLedger):
    """Baseline (a), and baseline (c) when attacked: the operator can rewrite votes."""

    def forge_vote(self, attacker, victim, case_id, step_id, endorse, reason=""):
        p = self.proposals[self._key(case_id, step_id)]
        p.votes[victim] = {"endorse": endorse, "reason": reason, "weight": self.cfg.weight_of(victim)}

    def drop_vote(self, attacker, victim, case_id, step_id):
        self.proposals[self._key(case_id, step_id)].votes.pop(victim, None)


class LedgerlessVoting(_NoLedger):
    """Baseline (b): signed votes, verified by the tallying party, no ledger."""

    def __init__(self, orgs=("Org1MSP", "Org2MSP", "Org3MSP")):
        super().__init__()
        # Each organisation holds its own signing key; the tally sees only public keys.
        self._keys = {o: Ed25519PrivateKey.generate() for o in orgs}
        self.public_keys = {o: k.public_key() for o, k in self._keys.items()}

    @staticmethod
    def _message(org, case_id, step_id, endorse) -> bytes:
        return f"{org}|{case_id}|{step_id}|{int(bool(endorse))}".encode()

    def _accept(self, claimed_org, case_id, step_id, endorse, reason, signature):
        try:
            self.public_keys[claimed_org].verify(signature, self._message(claimed_org, case_id, step_id, endorse))
        except InvalidSignature as e:
            raise ForgeryRejected(f"signature does not verify for {claimed_org}") from e
        LocalLedger.vote_step(self, claimed_org, case_id, step_id, endorse, reason)

    def vote_step(self, org, case_id, step_id, endorse, reason=""):
        sig = self._keys[org].sign(self._message(org, case_id, step_id, endorse))
        self._accept(org, case_id, step_id, endorse, reason, sig)

    def forge_vote(self, attacker, victim, case_id, step_id, endorse, reason=""):
        # The operator replaces the victim's vote with one signed by its own key.
        p = self.proposals[self._key(case_id, step_id)]
        honest = p.votes.pop(victim, None)
        sig = self._keys[attacker].sign(self._message(victim, case_id, step_id, endorse))
        try:
            self._accept(victim, case_id, step_id, endorse, reason, sig)
        except ForgeryRejected:
            if honest is not None:
                p.votes[victim] = honest
            raise

    def drop_vote(self, attacker, victim, case_id, step_id):
        # A valid signature proves who voted, not that the tally kept the vote:
        # the tallying party can discard it and nobody else holds the record.
        self.proposals[self._key(case_id, step_id)].votes.pop(victim, None)


COORDINATORS = {
    "orchestrator": TrustedOrchestrator,
    "ledgerless": LedgerlessVoting,
}
