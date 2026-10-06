import api from "../lib/api";

function failure(error) {
    const detail = error?.response?.data?.detail;
    const result = new Error(detail?.message || (typeof detail === "string" ? detail : null)
        || "تعذّر تنفيذ العملية. أعد المحاولة.");
    result.status = error?.response?.status;
    result.code = detail?.code;
    return result;
}

export async function getPendingSallaEdits(orderNumber) {
    try {
        return (await api.get(`/order-change-edit-v1/orders/${encodeURIComponent(orderNumber)}/pending`)).data;
    } catch (error) { throw failure(error); }
}

export async function applyPendingSallaEdit(orderNumber, payload) {
    try {
        return (await api.post(`/order-change-edit-v1/orders/${encodeURIComponent(orderNumber)}/apply`, payload)).data;
    } catch (error) { throw failure(error); }
}
