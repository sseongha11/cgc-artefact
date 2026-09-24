/*
 * Copyright IBM Corp. All Rights Reserved.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

'use strict';

const crypto = require('crypto');

const adminUserId = 'admin';
const adminUserPasswd = 'adminpw';
// One wallet serves all orgs, so each org's CA admin needs its own label;
// a shared 'admin' label would make Org2/Org3 registrations use Org1's admin.
const adminLabel = (orgMspId) => `${orgMspId}-admin`;

/**
 *
 * @param {*} FabricCAServices
 * @param {*} ccp
 */
exports.buildCAClient = (FabricCAServices, ccp, caHostName) => {
	// Create a new CA client for interacting with the CA.
	const caInfo = ccp.certificateAuthorities[caHostName]; //lookup CA details from config
	const caTLSCACerts = caInfo.tlsCACerts.pem;
	const caClient = new FabricCAServices(caInfo.url, { trustedRoots: caTLSCACerts, verify: true }, caInfo.caName);

	console.log(`Built a CA Client named ${caInfo.caName}`);
	return caClient;
};

exports.enrollAdmin = async (caClient, wallet, orgMspId) => {
	try {
		// Check to see if we've already enrolled the admin user.
		const identity = await wallet.get(adminLabel(orgMspId));
		if (identity) {
			console.log('An identity for the admin user already exists in the wallet');
			return;
		}

		console.log("Admin Identity not found... Enroll admin")
		// Enroll the admin user, and import the new identity into the wallet.
		const enrollment = await caClient.enroll({ enrollmentID: adminUserId, enrollmentSecret: adminUserPasswd });
		const x509Identity = {
			credentials: {
				certificate: enrollment.certificate,
				privateKey: enrollment.key.toBytes(),
			},
			mspId: orgMspId,
			type: 'X.509',
		};

		console.log("x509Id",x509Identity)
		console.log("putting into wallet")
		await wallet.put(adminLabel(orgMspId), x509Identity);
		console.log('Successfully enrolled admin user and imported it into the wallet');
	} catch (error) {
		console.error(`Failed to enroll admin user : ${error}`);
		throw error;
	}
};

exports.registerAndEnrollUser = async (caClient, wallet, orgMspId, userId, affiliation) => {
	// Check to see if we've already enrolled the user
	const userIdentity = await wallet.get(userId);
	if (userIdentity) {
		console.log(`An identity for the user ${userId} already exists in the wallet`);
		return;
	}

	// Must use an admin to register a new user
	const adminIdentity = await wallet.get(adminLabel(orgMspId));
	if (!adminIdentity) {
		throw new Error(`no ${orgMspId} admin identity in the wallet; enroll the admin first`);
	}

	// build a user object for authenticating with the CA
	const provider = wallet.getProviderRegistry().getProvider(adminIdentity.type);
	const adminUser = await provider.getUserContext(adminIdentity, adminLabel(orgMspId));

	// Register the user, enroll the user, and import the new identity into the wallet.
	// If the CA already knows the user (e.g. a fresh wallet on a gateway that
	// replaced an older API), the org admin resets its secret and re-enrolls it.
	let secret;
	try {
		secret = await caClient.register({
			affiliation: affiliation,
			enrollmentID: userId,
			role: 'client'
		}, adminUser);
	} catch (error) {
		if (!/already registered/i.test(String(error))) {
			throw error;
		}
		secret = crypto.randomBytes(16).toString('hex');
		// The SDK registers identities with a cap of one enrollment, so lift the cap too.
		await caClient.newIdentityService().update(userId, { enrollmentSecret: secret, maxEnrollments: -1 }, adminUser);
		console.log(`${userId} was already registered; reset its secret to re-enroll`);
	}
	const enrollment = await caClient.enroll({
		enrollmentID: userId,
		enrollmentSecret: secret
	});
	const x509Identity = {
		credentials: {
			certificate: enrollment.certificate,
			privateKey: enrollment.key.toBytes(),
		},
		mspId: orgMspId,
		type: 'X.509',
	};
	await wallet.put(userId, x509Identity);
	console.log(`Successfully registered and enrolled user ${userId} and imported it into the wallet`);
};


exports.userExist=async(wallet,userId)=>{
	console.log("userExist: wallet path",wallet)
	const identity = await wallet.get(userId);
	if (!identity) {
		throw new Error("Identity not exist ")
	}
	return true;
}