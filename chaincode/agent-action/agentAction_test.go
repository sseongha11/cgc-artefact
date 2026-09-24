// Unit tests for the AgentAction chaincode.
//
// Same strategy as the asset-transfer tests: hand-rolled stubs implementing only
// the interface methods the chaincode uses, so `go test ./...` runs on a stock Go
// install with no gomock. Adds a ClientIdentity stub so each call can act as a
// specific org (MSPID), which is what the endorsement gate is all about.
package main

import (
	"crypto/x509"
	"fmt"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/golang/protobuf/ptypes/timestamp"
	"github.com/hyperledger/fabric-chaincode-go/pkg/cid"
	"github.com/hyperledger/fabric-chaincode-go/shim"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
	"github.com/hyperledger/fabric-protos-go/ledger/queryresult"
)

const (
	org1 = "Org1MSP" // Appellant
	org2 = "Org2MSP" // Planning officer
	org3 = "Org3MSP" // Inspector (mandatory for ROLE_WEIGHTED)
)

// ─── stubs ───────────────────────────────────────────────────────────────────

type historyEntry struct {
	value     []byte
	txID      string
	timestamp time.Time
	isDelete  bool
}

type stubState struct {
	state   map[string][]byte
	history map[string][]historyEntry
	seq     int
}

func newStore() *stubState {
	return &stubState{state: map[string][]byte{}, history: map[string][]historyEntry{}}
}

func (s *stubState) put(key string, value []byte, txID string) {
	s.state[key] = value
	s.history[key] = append(s.history[key], historyEntry{
		value: append([]byte(nil), value...), txID: txID, timestamp: time.Now(),
	})
}

type stubChaincodeStub struct {
	shim.ChaincodeStubInterface
	store *stubState
	txID  string
}

func (s *stubChaincodeStub) PutState(key string, value []byte) error {
	s.store.put(key, value, s.txID)
	return nil
}
func (s *stubChaincodeStub) GetState(key string) ([]byte, error) {
	v, ok := s.store.state[key]
	if !ok {
		return nil, nil
	}
	return v, nil
}

type rangeIter struct {
	keys  []string
	state *stubState
	i     int
}

func (r *rangeIter) HasNext() bool { return r.i < len(r.keys) }
func (r *rangeIter) Next() (*queryresult.KV, error) {
	k := r.keys[r.i]
	r.i++
	return &queryresult.KV{Key: k, Value: r.state.state[k]}, nil
}
func (r *rangeIter) Close() error { return nil }

// Naive stub: returns ALL keys (chaincode filters by prefix defensively).
func (s *stubChaincodeStub) GetStateByRange(start, end string) (shim.StateQueryIteratorInterface, error) {
	// The real shim panics on non-UTF-8 range keys (protobuf string fields).
	if !utf8.ValidString(start) || !utf8.ValidString(end) {
		return nil, fmt.Errorf("range key is not valid UTF-8: %q..%q", start, end)
	}
	keys := make([]string, 0, len(s.store.state))
	for k := range s.store.state {
		keys = append(keys, k)
	}
	sortStrings(keys)
	return &rangeIter{keys: keys, state: s.store}, nil
}

type histIter struct {
	entries []historyEntry
	i       int
}

func (h *histIter) HasNext() bool { return h.i < len(h.entries) }
func (h *histIter) Next() (*queryresult.KeyModification, error) {
	e := h.entries[h.i]
	h.i++
	return &queryresult.KeyModification{
		TxId: e.txID, Value: e.value,
		Timestamp: &timestamp.Timestamp{Seconds: e.timestamp.Unix()}, IsDelete: e.isDelete,
	}, nil
}
func (h *histIter) Close() error { return nil }

func (s *stubChaincodeStub) GetHistoryForKey(key string) (shim.HistoryQueryIteratorInterface, error) {
	return &histIter{entries: s.store.history[key]}, nil
}

func (s *stubChaincodeStub) GetTxTimestamp() (*timestamp.Timestamp, error) {
	return &timestamp.Timestamp{Seconds: time.Now().Unix()}, nil
}

// stubCID implements cid.ClientIdentity; only GetMSPID carries meaning.
type stubCID struct{ msp string }

func (c stubCID) GetID() (string, error)                         { return "x509::" + c.msp, nil }
func (c stubCID) GetMSPID() (string, error)                      { return c.msp, nil }
func (c stubCID) GetAttributeValue(string) (string, bool, error) { return "", false, nil }
func (c stubCID) AssertAttributeValue(string, string) error      { return nil }
func (c stubCID) GetX509Certificate() (*x509.Certificate, error) { return nil, nil }

type stubContext struct {
	contractapi.TransactionContext
	stub *stubChaincodeStub
	cid  stubCID
}

func (c *stubContext) GetStub() shim.ChaincodeStubInterface  { return c.stub }
func (c *stubContext) GetClientIdentity() cid.ClientIdentity { return c.cid }

// ctxAs returns a context sharing `store`, acting as org `msp`. Each call gets a
// fresh, monotonically increasing txID so history entries are distinct.
func ctxAs(store *stubState, msp string) *stubContext {
	store.seq++
	return &stubContext{
		stub: &stubChaincodeStub{store: store, txID: itoa(store.seq)},
		cid:  stubCID{msp: msp},
	}
}

func itoa(i int) string {
	if i == 0 {
		return "tx0"
	}
	digits := ""
	for i > 0 {
		digits = string(rune('0'+i%10)) + digits
		i /= 10
	}
	return "tx" + digits
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j-1] > s[j]; j-- {
			s[j-1], s[j] = s[j], s[j-1]
		}
	}
}

// ─── helpers ─────────────────────────────────────────────────────────────────

func mustSetPolicy(t *testing.T, c SmartContract, store *stubState, policy string, n int, mandatory string, reqWeight int, weights string) {
	t.Helper()
	if err := c.SetPolicy(ctxAs(store, org1), policy, n, mandatory, reqWeight, weights); err != nil {
		t.Fatalf("SetPolicy(%s): %v", policy, err)
	}
}

func mustPropose(t *testing.T, c SmartContract, store *stubState, as, caseID, step, action string) {
	t.Helper()
	if err := c.ProposeStep(ctxAs(store, as), caseID, step, action, "hash-"+action); err != nil {
		t.Fatalf("ProposeStep as %s: %v", as, err)
	}
}

func mustVote(t *testing.T, c SmartContract, store *stubState, as, caseID, step string, endorse bool) {
	t.Helper()
	if err := c.VoteStep(ctxAs(store, as), caseID, step, endorse, "reason"); err != nil {
		t.Fatalf("VoteStep as %s: %v", as, err)
	}
}

func finalize(t *testing.T, c SmartContract, store *stubState, caseID, step string) string {
	t.Helper()
	st, err := c.FinalizeStep(ctxAs(store, org1), caseID, step)
	if err != nil {
		t.Fatalf("FinalizeStep: %v", err)
	}
	return st
}

// ─── tests: config ───────────────────────────────────────────────────────────

func TestDefaultPolicyIsMajority(t *testing.T) {
	c, store := SmartContract{}, newStore()
	cfg, err := c.GetPolicy(ctxAs(store, org1))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Policy != PolicyMajority || cfg.TotalOrgs != 3 {
		t.Errorf("default = %+v, want MAJORITY/3", cfg)
	}
}

func TestSetPolicyRejectsUnknown(t *testing.T) {
	c, store := SmartContract{}, newStore()
	if err := c.SetPolicy(ctxAs(store, org1), "SOMETIMES", 3, "", 0, ""); err == nil {
		t.Error("expected error for unknown policy")
	}
}

func TestRoleWeightedRequiresMandatory(t *testing.T) {
	c, store := SmartContract{}, newStore()
	if err := c.SetPolicy(ctxAs(store, org1), PolicyRoleWeighted, 3, "", 2, ""); err == nil {
		t.Error("ROLE_WEIGHTED without mandatoryMSP should error")
	}
}

// ─── tests: propose / vote guards ────────────────────────────────────────────

func TestProposeThenDuplicateFails(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustPropose(t, c, store, org1, "case1", "decide", "APPROVE")
	if err := c.ProposeStep(ctxAs(store, org2), "case1", "decide", "APPROVE", "h"); err == nil {
		t.Error("duplicate ProposeStep should fail while OPEN")
	}
}

func TestDoubleVoteFails(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustPropose(t, c, store, org1, "case1", "decide", "APPROVE")
	mustVote(t, c, store, org2, "case1", "decide", true)
	if err := c.VoteStep(ctxAs(store, org2), "case1", "decide", false, "changed mind"); err == nil {
		t.Error("second vote by same org should fail")
	}
}

func TestVoteOnMissingStepFails(t *testing.T) {
	c, store := SmartContract{}, newStore()
	if err := c.VoteStep(ctxAs(store, org1), "case1", "decide", true, "r"); err == nil {
		t.Error("voting on a non-existent step should fail")
	}
}

// ─── tests: policy gate ──────────────────────────────────────────────────────

func TestMajorityCommitsOnTwoOfThree(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustSetPolicy(t, c, store, PolicyMajority, 3, "", 0, "")
	mustPropose(t, c, store, org1, "case1", "decide", "APPROVE")
	mustVote(t, c, store, org1, "case1", "decide", true)
	if st := finalize(t, c, store, "case1", "decide"); st != StatusOpen {
		t.Fatalf("after 1/3 endorse want OPEN, got %s", st)
	}
	mustVote(t, c, store, org2, "case1", "decide", true)
	if st := finalize(t, c, store, "case1", "decide"); st != StatusCommitted {
		t.Fatalf("after 2/3 endorse want COMMITTED, got %s", st)
	}
	p, _ := c.GetProposal(ctxAs(store, org1), "case1", "decide")
	if p.Outcome != "APPROVE" {
		t.Errorf("outcome = %q, want APPROVE", p.Outcome)
	}
}

func TestMajorityRejectsWhenTwoDissent(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustSetPolicy(t, c, store, PolicyMajority, 3, "", 0, "")
	mustPropose(t, c, store, org1, "case1", "decide", "APPROVE")
	mustVote(t, c, store, org1, "case1", "decide", true)
	mustVote(t, c, store, org2, "case1", "decide", false)
	mustVote(t, c, store, org3, "case1", "decide", false)
	if st := finalize(t, c, store, "case1", "decide"); st != StatusRejected {
		t.Fatalf("want REJECTED, got %s", st)
	}
}

func TestAnyCommitsOnSingleEndorse(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustSetPolicy(t, c, store, PolicyAny, 3, "", 0, "")
	mustPropose(t, c, store, org1, "case1", "decide", "APPROVE")
	mustVote(t, c, store, org1, "case1", "decide", true)
	if st := finalize(t, c, store, "case1", "decide"); st != StatusCommitted {
		t.Fatalf("ANY with 1 endorse want COMMITTED, got %s", st)
	}
}

func TestUnanimousNeedsAllAndAnyDissentRejects(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustSetPolicy(t, c, store, PolicyUnanimous, 3, "", 0, "")
	mustPropose(t, c, store, org1, "c", "decide", "APPROVE")
	mustVote(t, c, store, org1, "c", "decide", true)
	mustVote(t, c, store, org2, "c", "decide", true)
	if st := finalize(t, c, store, "c", "decide"); st != StatusOpen {
		t.Fatalf("2/3 under UNANIMOUS want OPEN, got %s", st)
	}
	mustVote(t, c, store, org3, "c", "decide", false)
	if st := finalize(t, c, store, "c", "decide"); st != StatusRejected {
		t.Fatalf("dissent under UNANIMOUS want REJECTED, got %s", st)
	}
}

func TestRoleWeightedMandatoryVeto(t *testing.T) {
	c, store := SmartContract{}, newStore()
	// Inspector (org3) mandatory; need total endorsing weight >= 2.
	mustSetPolicy(t, c, store, PolicyRoleWeighted, 3, org3, 2, "")
	mustPropose(t, c, store, org1, "c", "decide", "APPROVE")
	mustVote(t, c, store, org1, "c", "decide", true)
	mustVote(t, c, store, org2, "c", "decide", true)  // weight 2 reached, but...
	mustVote(t, c, store, org3, "c", "decide", false) // ...mandatory vetoes
	if st := finalize(t, c, store, "c", "decide"); st != StatusRejected {
		t.Fatalf("mandatory veto want REJECTED, got %s", st)
	}
}

func TestRoleWeightedCommitsWithMandatoryAndWeight(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustSetPolicy(t, c, store, PolicyRoleWeighted, 3, org3, 2, "")
	mustPropose(t, c, store, org1, "c", "decide", "APPROVE")
	mustVote(t, c, store, org3, "c", "decide", true) // mandatory endorses (weight 1)
	if st := finalize(t, c, store, "c", "decide"); st != StatusOpen {
		t.Fatalf("weight 1 < 2 want OPEN, got %s", st)
	}
	mustVote(t, c, store, org1, "c", "decide", true) // weight now 2
	if st := finalize(t, c, store, "c", "decide"); st != StatusCommitted {
		t.Fatalf("mandatory+weight2 want COMMITTED, got %s", st)
	}
}

// ─── test: blast-radius scenario (the paper's thesis in miniature) ───────────

func TestPoisonedProposerOutvotedUnderMajority(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustSetPolicy(t, c, store, PolicyMajority, 3, "", 0, "")
	// Org1 "hallucinates": proposes the WRONG action and endorses it...
	mustPropose(t, c, store, org1, "case1", "decide", "REJECT_WRONG")
	mustVote(t, c, store, org1, "case1", "decide", true)
	// ...but org2 and org3 reason correctly and dissent -> gate contains it.
	mustVote(t, c, store, org2, "case1", "decide", false)
	mustVote(t, c, store, org3, "case1", "decide", false)
	if st := finalize(t, c, store, "case1", "decide"); st != StatusRejected {
		t.Fatalf("poisoned proposal should NOT commit under MAJORITY, got %s", st)
	}
	// The dissent is on the immutable trail.
	p, _ := c.GetProposal(ctxAs(store, org1), "case1", "decide")
	if p.Votes[org2].Endorse || p.Votes[org3].Endorse {
		t.Error("expected recorded dissent from org2/org3")
	}
}

// ─── tests: trail / history / re-propose ─────────────────────────────────────

func TestCaseTrailReturnsAllStepsSorted(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustPropose(t, c, store, org1, "case1", "assess", "OK")
	mustPropose(t, c, store, org1, "case1", "decide", "APPROVE")
	mustPropose(t, c, store, org1, "case1", "intake", "CLASSIFIED")
	mustPropose(t, c, store, org2, "case2", "decide", "REJECT") // different case, excluded
	trail, err := c.GetCaseTrail(ctxAs(store, org1), "case1")
	if err != nil {
		t.Fatal(err)
	}
	if len(trail) != 3 {
		t.Fatalf("want 3 steps for case1, got %d", len(trail))
	}
	if trail[0].StepID != "assess" || trail[2].StepID != "intake" {
		t.Errorf("steps not sorted: %s..%s", trail[0].StepID, trail[2].StepID)
	}
}

func TestStepHistoryRecordsMutations(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustPropose(t, c, store, org1, "case1", "decide", "APPROVE")
	mustVote(t, c, store, org1, "case1", "decide", true)
	mustVote(t, c, store, org2, "case1", "decide", true)
	finalize(t, c, store, "case1", "decide")
	hist, err := c.GetStepHistory(ctxAs(store, org1), "case1", "decide")
	if err != nil {
		t.Fatal(err)
	}
	// propose + 2 votes + finalize = 4 writes
	if len(hist) != 4 {
		t.Fatalf("want 4 history entries, got %d", len(hist))
	}
	if hist[len(hist)-1].Record.Status != StatusCommitted {
		t.Errorf("last history state should be COMMITTED, got %s", hist[len(hist)-1].Record.Status)
	}
}

func TestReproposeBumpsRoundAndClearsVotes(t *testing.T) {
	c, store := SmartContract{}, newStore()
	mustSetPolicy(t, c, store, PolicyMajority, 3, "", 0, "")
	mustPropose(t, c, store, org1, "c", "decide", "APPROVE")
	mustVote(t, c, store, org1, "c", "decide", true)
	mustVote(t, c, store, org2, "c", "decide", false)
	mustVote(t, c, store, org3, "c", "decide", false)
	if st := finalize(t, c, store, "c", "decide"); st != StatusRejected {
		t.Fatalf("want REJECTED, got %s", st)
	}
	if err := c.ReproposeStep(ctxAs(store, org2), "c", "decide", "REJECT", "h2"); err != nil {
		t.Fatalf("ReproposeStep: %v", err)
	}
	p, _ := c.GetProposal(ctxAs(store, org1), "c", "decide")
	if p.Round != 2 || p.Status != StatusOpen || len(p.Votes) != 0 || p.ActionType != "REJECT" {
		t.Errorf("re-propose state wrong: %+v", p)
	}
}
