const express = require("express");
const app = express();
var morgan = require('morgan')
app.use(morgan('combined'))
const bodyparser = require("body-parser");
const { registerUser, userExist } = require("./registerUser");
const {createAsset,TransferAsset,updateAsset,deleteAsset} =require('./tx')
const {GetAllAssets,GetAssetHistory} =require('./query')
const agent = require('./agentAction')

const chaincodeName = "basic";
const channelName = "mychannel"
// AgentAction chaincode. Override via env when deployed alongside basic.
const agentChaincodeName = process.env.AGENT_CC_NAME || "agentaction";

// Build the standard payload for an AgentAction call from a request.
function agentPayload(req) {
    const src = req.method === 'GET' ? req.query : req.body;
    return {
        org: src.org,
        userId: src.userId,
        channelName: channelName,
        chaincodeName: agentChaincodeName,
        data: src.data !== undefined ? src.data : src,
    };
}

var cors = require('cors')
app.use(cors())
app.use(bodyparser.json());

// Per organisation gateway: when GATEWAY_MSP is set, this process serves only
// that org. Its wallet holds only that org's identities, and requests naming any
// other org are refused, so no single gateway can act for every organisation.
const gatewayMsp = process.env.GATEWAY_MSP;
if (gatewayMsp) {
    app.use((req, res, next) => {
        const src = req.method === 'GET' ? req.query : req.body;
        if (src && src.org && src.org !== gatewayMsp) {
            return res.status(403).send(`gateway for ${gatewayMsp} cannot act as ${src.org}`);
        }
        next();
    });
}

app.listen(4000, () => {
    console.log("server started");

})

app.post("/register", async (req, res) => {

    try {
        let org = req.body.org;
        let userId = req.body.userId;
        let result = await registerUser({ OrgMSP: org, userId: userId });
        res.send(result);

    } catch (error) {
        res.status(500).send(error)
    }
});


app.post("/createAsset", async (req, res) => {
    try {


        let payload = {
            "org": req.body.org,
            "channelName": channelName,
            "chaincodeName": chaincodeName,
            "userId": req.body.userId,
            "data": req.body.data
        }

        let result = await createAsset(payload);
        res.send(result)
    } catch (error) {
        res.status(500).send(error)
    }
})



app.post("/updateAsset", async (req, res) => {
    try {


        let payload = {
            "org": req.body.org,
            "channelName": channelName,
            "chaincodeName": chaincodeName,
            "userId": req.body.userId,
            "data": req.body.data
        }

        let result = await updateAsset(payload);
        res.send(result)
    } catch (error) {
        res.status(500).send(error)
    }
})


app.post("/transferAsset", async (req, res) => {

    try {

        let payload = {
            "org": req.body.org,
            "channelName": channelName,
            "chaincodeName": chaincodeName,
            "userId": req.body.userId,
            "data": req.body.data
        }

        let result = await TransferAsset(payload);
        res.send(result)
    } catch (error) {
        res.status(500).send(error)
    }
})


app.post("/deleteAsset", async (req, res) => {
    try {
        let payload = {
            "org": req.body.org,
            "channelName": channelName,
            "chaincodeName": chaincodeName,
            "userId": req.body.userId,
            "data": req.body.data
        }

        let result = await deleteAsset(payload);
        res.send(result)
    } catch (error) {
        res.status(500).send(error)
    }
})


app.get('/getAllAssets', async (req, res) => {
    try {


        let payload = {
            "org": req.query.org,
            "channelName": channelName,
            "chaincodeName": chaincodeName,
            "userId": req.query.userId
        }

        let result = await GetAllAssets(payload);
        res.json(result)
    } catch (error) {
        res.send(error)
    }
});

app.get('/getAssetHistory', async (req, res) => {
    try {
        let payload = {
            "org": req.query.org,
            "channelName": channelName,
            "chaincodeName": chaincodeName,
            "userId": req.query.userId,
            "data": {
                id: req.query.id
            }
        }

        let result = await GetAssetHistory(payload);
        res.json(result)
    } catch (error) {
        res.status(500).send(error)
    }

});


// ─── AgentAction chaincode routes ────────────────────────────────
// Each expects { org, userId, data:{...} }. `data` fields per handler in
// agentAction.js. Reads accept the same via query string.

app.post('/setPolicy', async (req, res) => {
    try { res.json(await agent.SetPolicy(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.get('/getPolicy', async (req, res) => {
    try { res.json(await agent.GetPolicy(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.post('/proposeStep', async (req, res) => {
    try { res.json(await agent.ProposeStep(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.post('/voteStep', async (req, res) => {
    try { res.json(await agent.VoteStep(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.post('/finalizeStep', async (req, res) => {
    try { res.json(await agent.FinalizeStep(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.post('/reproposeStep', async (req, res) => {
    try { res.json(await agent.ReproposeStep(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.get('/getProposal', async (req, res) => {
    try { res.json(await agent.GetProposal(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.get('/caseTrail', async (req, res) => {
    try { res.json(await agent.GetCaseTrail(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});

app.get('/stepHistory', async (req, res) => {
    try { res.json(await agent.GetStepHistory(agentPayload(req))); }
    catch (e) { res.status(500).send(String(e)); }
});


