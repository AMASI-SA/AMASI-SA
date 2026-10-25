import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

function endpoint(accountId) {
    const value = String(accountId || "");
    if (!/^[0-9]{1,30}$/.test(value)) throw new Error("اختر حساب جوجل أولًا.");
    return `${GOOGLE_ADS_ROOT}/automation/policy/${value}`;
}

export async function getGoogleAutomationPolicy(accountId, { signal } = {}) {
    return (await api.get(endpoint(accountId), { signal })).data;
}

export async function saveGoogleAutomationPolicy(accountId, payload, { signal } = {}) {
    return (await api.put(endpoint(accountId), { ...payload, enabled: false }, { signal })).data;
}

export async function revokeGoogleAutomationPolicy(accountId, expectedRevision, { signal } = {}) {
    return (await api.post(`${endpoint(accountId)}/revoke`, { expected_revision: expectedRevision }, { signal })).data;
}
