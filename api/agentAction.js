// AgentAction chaincode client.
//
// Same Fabric-gateway pattern as tx.js / query.js, factored into one helper so
// each transaction is a two-line wrapper. Every call runs under the caller's org
// identity (derived from the org MSP, exactly like the asset-transfer routes),
// which is what makes the endorsement gate meaningful across organisations.
const { getCCP } = require("./buildCCP");
const { Wallets, Gateway } = require("fabric-network");
const path = require("path");
const walletPath = path.join(__dirname, "wallet");
const { buildWallet } = require("./AppUtils");

// Run `fn(contract)` connected as request.userId under request.org. `submit`
// picks submitTransaction (writes) vs evaluateTransaction (reads).
async function withContract(request, fn) {
  const num = Number(request.org.match(/\d/g).join(""));
  const ccp = getCCP(num);
  const wallet = await buildWallet(Wallets, walletPath);
  const gateway = new Gateway();
  try {
    await gateway.connect(ccp, {
      wallet,
      identity: request.userId,
      discovery: { enabled: true, asLocalhost: false },
    });
    const network = await gateway.getNetwork(request.channelName);
    const contract = network.getContract(request.chaincodeName);
    return await fn(contract);
  } finally {
    gateway.disconnect();
  }
}

const toStr = (v) => (typeof v === "string" ? v : JSON.stringify(v));
const parse = (buf) => {
  const s = buf.toString();
  try {
    return JSON.parse(s);
  } catch (_) {
    return s; // FinalizeStep returns a bare status string
  }
};

// ── config ──────────────────────────────────────────────────────────────────

// data: { policy, totalOrgs, mandatoryMSP?, requiredWeight?, weights? }
exports.SetPolicy = (request) =>
  withContract(request, async (c) => {
    const d = request.data;
    const res = await c.submitTransaction(
      "SetPolicy",
      d.policy,
      String(d.totalOrgs),
      d.mandatoryMSP || "",
      String(d.requiredWeight || 0),
      d.weights ? toStr(d.weights) : ""
    );
    return parse(res);
  });

exports.GetPolicy = (request) =>
  withContract(request, async (c) =>
    parse(await c.evaluateTransaction("GetPolicy"))
  );

// ── workflow ──────────────────────────────────────────────────────────────────

// data: { caseID, stepID, actionType, payloadHash }
exports.ProposeStep = (request) =>
  withContract(request, async (c) => {
    const d = request.data;
    const res = await c.submitTransaction(
      "ProposeStep",
      d.caseID,
      d.stepID,
      d.actionType,
      d.payloadHash || ""
    );
    return parse(res);
  });

// data: { caseID, stepID, endorse (bool), reason }
exports.VoteStep = (request) =>
  withContract(request, async (c) => {
    const d = request.data;
    const res = await c.submitTransaction(
      "VoteStep",
      d.caseID,
      d.stepID,
      d.endorse ? "true" : "false",
      d.reason || ""
    );
    return parse(res);
  });

// data: { caseID, stepID }  -> returns status string (COMMITTED|REJECTED|OPEN)
exports.FinalizeStep = (request) =>
  withContract(request, async (c) => {
    const d = request.data;
    const res = await c.submitTransaction("FinalizeStep", d.caseID, d.stepID);
    return parse(res);
  });

// data: { caseID, stepID, actionType, payloadHash }
exports.ReproposeStep = (request) =>
  withContract(request, async (c) => {
    const d = request.data;
    const res = await c.submitTransaction(
      "ReproposeStep",
      d.caseID,
      d.stepID,
      d.actionType,
      d.payloadHash || ""
    );
    return parse(res);
  });

// ── queries ───────────────────────────────────────────────────────────────────

exports.GetProposal = (request) =>
  withContract(request, async (c) =>
    parse(await c.evaluateTransaction("GetProposal", request.data.caseID, request.data.stepID))
  );

exports.GetCaseTrail = (request) =>
  withContract(request, async (c) =>
    parse(await c.evaluateTransaction("GetCaseTrail", request.data.caseID))
  );

exports.GetStepHistory = (request) =>
  withContract(request, async (c) =>
    parse(await c.evaluateTransaction("GetStepHistory", request.data.caseID, request.data.stepID))
  );
