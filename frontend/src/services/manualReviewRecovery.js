import api from "../lib/api";

export async function listManualReviewRecoveryCandidates() {
    return (await api.get("/order-reviews-v1/manual-recovery/candidates")).data;
}

export async function prepareManualReviewRecovery(orderNumber, oldOperationId) {
    return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/manual-recovery/prepare`, {
        old_operation_id: oldOperationId,
    })).data;
}

export async function confirmManualReviewRecovery(orderNumber, session) {
    return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/manual-recovery/confirm`, {
        session_id: session.session_id,
        approval_hash: session.approval_hash,
        confirmed: true,
    })).data;
}
