/*
SPDX-License-Identifier: Apache-2.0

AgentAction chaincode — consensus-gated action approval for cross-organisation
multi-agent workflows.

Each agent is the client of one Fabric org. A workflow step is *proposed* by one
agent (ProposeStep) and then *voted* on by the others (VoteStep), each vote backed
by that agent's own (LLM) judgement. FinalizeStep applies the channel's configured
endorsement policy (ANY / MAJORITY / UNANIMOUS / ROLE_WEIGHTED) to decide whether
the action commits — the gate is enforced here in chaincode + Fabric consensus, not
by any single trusted orchestrator. Every proposal, vote, and outcome is a ledger
write, so the whole decision trail is auditable (GetCaseTrail / GetStepHistory).

The policy is read from on-chain config (SetPolicy) so an experiment cell is one
Helm value, not a code change.
*/
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/golang/protobuf/ptypes"
	"github.com/hyperledger/fabric-chaincode-go/shim"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// ─── policy ──────────────────────────────────────────────────────────────────

const (
	PolicyAny          = "ANY"           // >=1 endorsement
	PolicyMajority     = "MAJORITY"      // > half of the orgs (2-of-3) — Fabric default
	PolicyUnanimous    = "UNANIMOUS"     // all orgs endorse
	PolicyRoleWeighted = "ROLE_WEIGHTED" // mandatory org must endorse AND weight >= threshold

	StatusOpen      = "OPEN"
	StatusCommitted = "COMMITTED"
	StatusRejected  = "REJECTED"

	configKey = "policy:config"
)

// PolicyConfig is the channel-wide gate configuration. Stored once (SetPolicy),
// read by every FinalizeStep so the run's policy is data, not code.
type PolicyConfig struct {
	Policy         string         `json:"policy"`         // one of the Policy* constants
	TotalOrgs      int            `json:"totalOrgs"`      // N — the collaborating orgs
	MandatoryMSP   string         `json:"mandatoryMSP"`   // ROLE_WEIGHTED: org that must endorse
	RequiredWeight int            `json:"requiredWeight"` // ROLE_WEIGHTED: min endorsing weight
	Weights        map[string]int `json:"weights"`        // per-MSP vote weight (default 1)
}

func defaultConfig() PolicyConfig {
	return PolicyConfig{Policy: PolicyMajority, TotalOrgs: 3, Weights: map[string]int{}}
}

func (c PolicyConfig) weightOf(msp string) int {
	if w, ok := c.Weights[msp]; ok {
		return w
	}
	return 1
}

// ─── data model ──────────────────────────────────────────────────────────────

// Vote is one org's judgement on a proposed step. Reason is a short justification
// (the LLM's rationale); the full model output lives off-chain, referenced by the
// proposal's PayloadHash, so endorsing peers stay deterministic.
type Vote struct {
	MSPID     string    `json:"mspid"`
	Endorse   bool      `json:"endorse"`
	Reason    string    `json:"reason"`
	Weight    int       `json:"weight"`
	Timestamp time.Time `json:"timestamp"`
}

// Proposal is one workflow step awaiting endorsement.
type Proposal struct {
	CaseID      string          `json:"caseID"`
	StepID      string          `json:"stepID"`
	Proposer    string          `json:"proposer"`   // MSPID that proposed it
	ActionType  string          `json:"actionType"` // proposed action / verdict, e.g. "APPROVE"
	PayloadHash string          `json:"payloadHash"`
	Policy      string          `json:"policy"` // policy in force when finalised (audit)
	Round       int             `json:"round"`  // re-proposal round (conflict-resolution cost)
	Status      string          `json:"status"` // OPEN | COMMITTED | REJECTED
	Outcome     string          `json:"outcome"`
	Votes       map[string]Vote `json:"votes"` // MSPID -> vote
}

// HistoryQueryResult mirrors the asset-transfer chaincode's shape for GetStepHistory.
type HistoryQueryResult struct {
	Record    *Proposal `json:"record"`
	TxID      string    `json:"txId"`
	Timestamp time.Time `json:"timestamp"`
	IsDelete  bool      `json:"isDelete"`
}

type SmartContract struct {
	contractapi.Contract
}

func proposalKey(caseID, stepID string) string {
	return fmt.Sprintf("prop:%s:%s", caseID, stepID)
}

// ─── config ──────────────────────────────────────────────────────────────────

// SetPolicy stores the channel-wide endorsement policy. `weightsJSON` is an
// optional JSON object of MSPID->weight (pass "" for uniform weight 1).
func (s *SmartContract) SetPolicy(ctx contractapi.TransactionContextInterface,
	policy string, totalOrgs int, mandatoryMSP string, requiredWeight int, weightsJSON string) error {

	switch policy {
	case PolicyAny, PolicyMajority, PolicyUnanimous, PolicyRoleWeighted:
	default:
		return fmt.Errorf("unknown policy %q", policy)
	}
	if totalOrgs < 1 {
		return fmt.Errorf("totalOrgs must be >= 1")
	}
	cfg := PolicyConfig{
		Policy: policy, TotalOrgs: totalOrgs,
		MandatoryMSP: mandatoryMSP, RequiredWeight: requiredWeight,
		Weights: map[string]int{},
	}
	if strings.TrimSpace(weightsJSON) != "" {
		if err := json.Unmarshal([]byte(weightsJSON), &cfg.Weights); err != nil {
			return fmt.Errorf("bad weightsJSON: %v", err)
		}
	}
	if policy == PolicyRoleWeighted && mandatoryMSP == "" {
		return fmt.Errorf("ROLE_WEIGHTED requires a mandatoryMSP")
	}
	b, err := json.Marshal(cfg)
	if err != nil {
		return err
	}
	return ctx.GetStub().PutState(configKey, b)
}

// GetPolicy returns the current config (defaults to MAJORITY/3 if never set).
func (s *SmartContract) GetPolicy(ctx contractapi.TransactionContextInterface) (*PolicyConfig, error) {
	cfg, err := loadConfig(ctx)
	if err != nil {
		return nil, err
	}
	return &cfg, nil
}

func loadConfig(ctx contractapi.TransactionContextInterface) (PolicyConfig, error) {
	b, err := ctx.GetStub().GetState(configKey)
	if err != nil {
		return PolicyConfig{}, err
	}
	if b == nil {
		return defaultConfig(), nil
	}
	var cfg PolicyConfig
	if err := json.Unmarshal(b, &cfg); err != nil {
		return PolicyConfig{}, err
	}
	if cfg.Weights == nil {
		cfg.Weights = map[string]int{}
	}
	return cfg, nil
}

// ─── workflow ────────────────────────────────────────────────────────────────

// ProposeStep records a new step proposal by the calling org. It does NOT
// auto-endorse — the proposer casts a vote like anyone else, so the gate always
// reflects explicit, counted judgements.
func (s *SmartContract) ProposeStep(ctx contractapi.TransactionContextInterface,
	caseID, stepID, actionType, payloadHash string) error {

	if caseID == "" || stepID == "" {
		return fmt.Errorf("caseID and stepID are required")
	}
	if strings.ContainsAny(caseID, ":") || strings.ContainsAny(stepID, ":") {
		return fmt.Errorf("caseID/stepID must not contain ':'")
	}
	msp, err := callerMSP(ctx)
	if err != nil {
		return err
	}
	key := proposalKey(caseID, stepID)
	existing, err := ctx.GetStub().GetState(key)
	if err != nil {
		return err
	}
	if existing != nil {
		var p Proposal
		if err := json.Unmarshal(existing, &p); err != nil {
			return err
		}
		if p.Status == StatusOpen {
			return fmt.Errorf("step %s/%s already open (round %d); use ReproposeStep", caseID, stepID, p.Round)
		}
		return fmt.Errorf("step %s/%s already decided (%s)", caseID, stepID, p.Status)
	}
	p := Proposal{
		CaseID: caseID, StepID: stepID, Proposer: msp,
		ActionType: actionType, PayloadHash: payloadHash,
		Round: 1, Status: StatusOpen, Votes: map[string]Vote{},
	}
	return putProposal(ctx, key, &p)
}

// VoteStep records the calling org's endorse/reject judgement on an open step.
// One vote per org per round.
func (s *SmartContract) VoteStep(ctx contractapi.TransactionContextInterface,
	caseID, stepID string, endorse bool, reason string) error {

	msp, err := callerMSP(ctx)
	if err != nil {
		return err
	}
	p, err := getProposal(ctx, caseID, stepID)
	if err != nil {
		return err
	}
	if p.Status != StatusOpen {
		return fmt.Errorf("step %s/%s is %s, not open for voting", caseID, stepID, p.Status)
	}
	if _, voted := p.Votes[msp]; voted {
		return fmt.Errorf("org %s already voted on %s/%s round %d", msp, caseID, stepID, p.Round)
	}
	cfg, err := loadConfig(ctx)
	if err != nil {
		return err
	}
	ts, _ := txTimestamp(ctx)
	p.Votes[msp] = Vote{
		MSPID: msp, Endorse: endorse, Reason: reason,
		Weight: cfg.weightOf(msp), Timestamp: ts,
	}
	return putProposal(ctx, proposalKey(caseID, stepID), p)
}

// FinalizeStep applies the configured policy and returns the resulting status.
// COMMITTED / REJECTED are terminal; OPEN means "still awaiting decisive votes".
func (s *SmartContract) FinalizeStep(ctx contractapi.TransactionContextInterface,
	caseID, stepID string) (string, error) {

	p, err := getProposal(ctx, caseID, stepID)
	if err != nil {
		return "", err
	}
	if p.Status != StatusOpen {
		return p.Status, nil // idempotent
	}
	cfg, err := loadConfig(ctx)
	if err != nil {
		return "", err
	}
	decision := evaluate(p, cfg)
	if decision == StatusOpen {
		return StatusOpen, nil
	}
	p.Status = decision
	p.Policy = cfg.Policy
	if decision == StatusCommitted {
		p.Outcome = p.ActionType
	}
	if err := putProposal(ctx, proposalKey(caseID, stepID), p); err != nil {
		return "", err
	}
	return decision, nil
}

// ReproposeStep re-opens a REJECTED step for another attempt with a fresh
// proposal, incrementing Round and clearing votes. This is the on-chain record
// of conflict-resolution cost.
func (s *SmartContract) ReproposeStep(ctx contractapi.TransactionContextInterface,
	caseID, stepID, actionType, payloadHash string) error {

	msp, err := callerMSP(ctx)
	if err != nil {
		return err
	}
	p, err := getProposal(ctx, caseID, stepID)
	if err != nil {
		return err
	}
	if p.Status == StatusCommitted {
		return fmt.Errorf("step %s/%s already committed; cannot re-propose", caseID, stepID)
	}
	p.Proposer = msp
	p.ActionType = actionType
	p.PayloadHash = payloadHash
	p.Round++
	p.Status = StatusOpen
	p.Outcome = ""
	p.Votes = map[string]Vote{}
	return putProposal(ctx, proposalKey(caseID, stepID), p)
}

// ─── policy evaluation ───────────────────────────────────────────────────────

// evaluate returns COMMITTED, REJECTED, or OPEN (undecided) for a proposal.
func evaluate(p *Proposal, cfg PolicyConfig) string {
	endorsers, endorseWeight := 0, 0
	dissent := 0
	mandatoryEndorsed := false
	mandatoryDissented := false
	for _, v := range p.Votes {
		if v.Endorse {
			endorsers++
			endorseWeight += v.Weight
			if v.MSPID == cfg.MandatoryMSP {
				mandatoryEndorsed = true
			}
		} else {
			dissent++
			if v.MSPID == cfg.MandatoryMSP {
				mandatoryDissented = true
			}
		}
	}
	n := cfg.TotalOrgs
	voted := len(p.Votes)
	remaining := n - voted
	if remaining < 0 {
		remaining = 0
	}

	switch cfg.Policy {
	case PolicyAny:
		if endorsers >= 1 {
			return StatusCommitted
		}
		if remaining == 0 {
			return StatusRejected
		}
	case PolicyMajority:
		need := n/2 + 1
		if endorsers >= need {
			return StatusCommitted
		}
		if endorsers+remaining < need {
			return StatusRejected
		}
	case PolicyUnanimous:
		if dissent > 0 {
			return StatusRejected // any dissent is fatal
		}
		if endorsers >= n {
			return StatusCommitted
		}
	case PolicyRoleWeighted:
		if mandatoryDissented {
			return StatusRejected // mandatory org's veto
		}
		if mandatoryEndorsed && endorseWeight >= cfg.RequiredWeight {
			return StatusCommitted
		}
		// impossible if the mandatory org can no longer endorse, or weight unreachable
		maxReachableWeight := endorseWeight + remaining // optimistic: each remaining weight>=1
		if (!mandatoryEndorsed && remaining == 0) || maxReachableWeight < cfg.RequiredWeight {
			return StatusRejected
		}
	}
	return StatusOpen
}

// ─── queries ─────────────────────────────────────────────────────────────────

// GetProposal returns the current state of one step.
func (s *SmartContract) GetProposal(ctx contractapi.TransactionContextInterface,
	caseID, stepID string) (*Proposal, error) {
	return getProposal(ctx, caseID, stepID)
}

// GetCaseTrail returns every step proposal for a case (current state), sorted by
// stepID. The per-step mutation history is available via GetStepHistory.
func (s *SmartContract) GetCaseTrail(ctx contractapi.TransactionContextInterface,
	caseID string) ([]*Proposal, error) {

	prefix := fmt.Sprintf("prop:%s:", caseID)
	// End key must be valid UTF-8: the shim panics marshalling a raw 0xff byte.
	// utf8.MaxRune is the sentinel Fabric itself uses for composite-key ranges.
	iter, err := ctx.GetStub().GetStateByRange(prefix, prefix+string(utf8.MaxRune))
	if err != nil {
		return nil, err
	}
	defer iter.Close()

	var out []*Proposal
	for iter.HasNext() {
		kv, err := iter.Next()
		if err != nil {
			return nil, err
		}
		if !strings.HasPrefix(kv.Key, prefix) { // defensive: naive stubs ignore range
			continue
		}
		var p Proposal
		if err := json.Unmarshal(kv.Value, &p); err != nil {
			return nil, err
		}
		out = append(out, &p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].StepID < out[j].StepID })
	return out, nil
}

// GetStepHistory returns the full chain of modifications for one step — the
// auditable record of who proposed, who voted, and how it resolved.
func (s *SmartContract) GetStepHistory(ctx contractapi.TransactionContextInterface,
	caseID, stepID string) ([]HistoryQueryResult, error) {

	iter, err := ctx.GetStub().GetHistoryForKey(proposalKey(caseID, stepID))
	if err != nil {
		return nil, err
	}
	defer iter.Close()

	var records []HistoryQueryResult
	for iter.HasNext() {
		resp, err := iter.Next()
		if err != nil {
			return nil, err
		}
		var p Proposal
		if len(resp.Value) > 0 {
			if err := json.Unmarshal(resp.Value, &p); err != nil {
				return nil, err
			}
		}
		ts, err := ptypes.Timestamp(resp.Timestamp)
		if err != nil {
			return nil, err
		}
		records = append(records, HistoryQueryResult{
			Record: &p, TxID: resp.TxId, Timestamp: ts, IsDelete: resp.IsDelete,
		})
	}
	return records, nil
}

// ─── helpers ─────────────────────────────────────────────────────────────────

func callerMSP(ctx contractapi.TransactionContextInterface) (string, error) {
	msp, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return "", fmt.Errorf("cannot read caller MSPID: %v", err)
	}
	if msp == "" {
		return "", fmt.Errorf("empty caller MSPID")
	}
	return msp, nil
}

func txTimestamp(ctx contractapi.TransactionContextInterface) (time.Time, error) {
	ts, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		return time.Time{}, err
	}
	return ptypes.Timestamp(ts)
}

func getProposal(ctx contractapi.TransactionContextInterface, caseID, stepID string) (*Proposal, error) {
	b, err := ctx.GetStub().GetState(proposalKey(caseID, stepID))
	if err != nil {
		return nil, err
	}
	if b == nil {
		return nil, fmt.Errorf("step %s/%s does not exist", caseID, stepID)
	}
	var p Proposal
	if err := json.Unmarshal(b, &p); err != nil {
		return nil, err
	}
	if p.Votes == nil {
		p.Votes = map[string]Vote{}
	}
	return &p, nil
}

func putProposal(ctx contractapi.TransactionContextInterface, key string, p *Proposal) error {
	b, err := json.Marshal(p)
	if err != nil {
		return err
	}
	return ctx.GetStub().PutState(key, b)
}

// ─── server ──────────────────────────────────────────────────────────────────

type serverConfig struct {
	CCID    string
	Address string
}

func main() {
	config := serverConfig{
		CCID:    os.Getenv("CHAINCODE_ID"),
		Address: os.Getenv("CHAINCODE_SERVER_ADDRESS"),
	}
	chaincode, err := contractapi.NewChaincode(&SmartContract{})
	if err != nil {
		log.Panicf("error creating agent-action chaincode: %s", err)
	}
	server := &shim.ChaincodeServer{
		CCID:     config.CCID,
		Address:  config.Address,
		CC:       chaincode,
		TLSProps: shim.TLSProperties{Disabled: true},
	}
	if err := server.Start(); err != nil {
		log.Panicf("error starting agent-action chaincode: %s", err)
	}
}
