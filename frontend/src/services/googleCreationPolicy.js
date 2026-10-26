import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

function endpoint(accountId) {
    const id = String(accountId || "");
    if (!/^[0-9]{1,20}$/.test(id)) throw new Error("اختر حساب جوجل أولًا.");
    return `${GOOGLE_ADS_ROOT}/automation/creation-policy/${id}`;
}
export const getGoogleCreationPolicy = (accountId, { signal } = {}) => api.get(endpoint(accountId), { signal }).then((result) => result.data);
export const saveGoogleCreationPolicy = (accountId, payload, { signal } = {}) => api.put(endpoint(accountId), { ...payload, enabled: false }, { signal }).then((result) => result.data);
export const revokeGoogleCreationPolicy = (accountId, revision, { signal } = {}) => api.post(`${endpoint(accountId)}/revoke`, { expected_revision: revision }, { signal }).then((result) => result.data);
export function activateGoogleCreationPolicy(accountId, revision, acknowledgements, { signal } = {}) {
    if (acknowledgements?.creation !== true || acknowledgements?.creative !== true || typeof acknowledgements?.autoEnable !== "boolean") {
        return Promise.reject(new Error("راجع الإنشاء والقالب وحدود التفعيل واعتمدها صراحةً."));
    }
    return api.post(`${endpoint(accountId)}/activate`, { expected_revision: revision,
        acknowledge_creation: true, acknowledge_creative: true, acknowledge_auto_enable: acknowledgements.autoEnable,
    }, { signal }).then((result) => result.data);
}
