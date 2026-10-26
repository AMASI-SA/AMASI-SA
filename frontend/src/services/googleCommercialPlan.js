import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

function endpoint(planId) {
    const id = String(planId || "");
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) throw new Error("معرف الخطة غير صالح.");
    return `${GOOGLE_ADS_ROOT}/commercial-plans/${id}`;
}

export async function getGoogleCommercialPlan(planId, { signal } = {}) {
    try {
        return (await api.get(endpoint(planId), { signal })).data;
    } catch (error) {
        if (error?.response?.status === 404 && error?.response?.data?.detail?.code === "google_commercial_plan_not_found") return null;
        throw error;
    }
}

export async function saveGoogleCommercialPlan(planId, payload, { signal } = {}) {
    if (payload.acknowledge_review !== true) throw new Error("راجع حقول الخطة قبل الحفظ.");
    return (await api.put(endpoint(planId), payload, { signal })).data;
}
