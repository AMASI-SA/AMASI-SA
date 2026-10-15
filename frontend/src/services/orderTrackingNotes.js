import api from "../lib/api";

export async function searchTrackedOrders(query, limit = 20) {
    return (await api.get("/order-tracking-notes/search", {
        params: { q: query, limit },
    })).data;
}

export async function getTrackedOrder(orderNumber) {
    return (await api.get(`/order-tracking-notes/orders/${encodeURIComponent(orderNumber)}`)).data;
}

export async function createTrackingInstruction(orderNumber, payload) {
    return (await api.post(
        `/order-tracking-notes/orders/${encodeURIComponent(orderNumber)}/instructions`,
        payload,
    )).data;
}

export async function acknowledgeTrackingInstruction(instructionId) {
    return (await api.post(
        `/order-tracking-notes/instructions/${encodeURIComponent(instructionId)}/acknowledge`,
    )).data;
}

export async function completeTrackingInstruction(instructionId, note = "") {
    return (await api.post(
        `/order-tracking-notes/instructions/${encodeURIComponent(instructionId)}/complete`,
        { note: note || null },
    )).data;
}

export async function uploadTrackingInstructionEvidence(instructionId, files, resultNote = "") {
    const body = new FormData();
    for (const file of files || []) body.append("files", file);
    body.append("result_note", resultNote || "");
    return (await api.post(
        `/order-tracking-notes/instructions/${encodeURIComponent(instructionId)}/evidence`,
        body,
        { headers: { "Content-Type": "multipart/form-data" } },
    )).data;
}

export async function approveTrackingInstruction(instructionId, note = "") {
    return (await api.post(
        `/order-tracking-notes/instructions/${encodeURIComponent(instructionId)}/approve`,
        { note: note || null },
    )).data;
}

export async function rejectTrackingInstruction(instructionId, note) {
    return (await api.post(
        `/order-tracking-notes/instructions/${encodeURIComponent(instructionId)}/reject`,
        { note },
    )).data;
}
