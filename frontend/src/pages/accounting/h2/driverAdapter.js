import api from "../../../lib/api";

export const DRIVER_BASE = "/accounting-module/shipping-v2";
export const REVIEW_QUEUE = "/store-delivery/payment-review/pending";
export const DRIVER_BLOCKERS = {
    history: "driver_payment_review_history_read_contract_missing",
    cash: "driver_cash_custody_read_contract_missing",
};
export async function loadDriverContext() {
    const { data } = await api.get(`${DRIVER_BASE}/context`);
    if (!Array.isArray(data?.store_drivers) || !Array.isArray(data?.couriers) || !data?.bank_port || !data?.driver_payment_destination) throw new Error("shipping_context_contract_unavailable");
    return data;
}
export async function loadDriverReviews() {
    const { data } = await api.get(REVIEW_QUEUE, { params: { limit: 250 } });
    if (!Array.isArray(data?.items) || data.items.some(row => row.status !== "pending")) throw new Error("driver_pending_review_contract_unavailable");
    return data.items;
}
export async function loadDriverStatement(kind, identity) {
    if (!["store_driver", "courier"].includes(kind) || !identity) throw new Error("shipping_identity_required");
    const { data } = await api.get(`${DRIVER_BASE}/statements/${kind}/${encodeURIComponent(identity)}`);
    if (data?.ledger_source !== "accounting_v2" || data.party_type !== kind || data.party_id !== identity) throw new Error("shipping_native_statement_required");
    return data;
}
export function driverFailure(error) {
    const status = error?.response?.status;
    return { blocked: [404, 409, 423, 501, 503].includes(status) || ["shipping_context_contract_unavailable", "driver_pending_review_contract_unavailable", "shipping_native_statement_required", "shipping_identity_required"].includes(error?.message),
        code: error?.response?.data?.detail?.code || error?.message || "driver_read_unavailable" };
}
export function reviewPresentation(row) {
    if (row.status === "pending") return { label: "بانتظار المراجعة — ليس تحصيلاً نهائيًا", effect: "لا يثبت رفع الدليل وصول الأموال أو تسوية مسؤولية الموصل." };
    if (row.status === "rejected") return { label: "مرفوض", effect: row.financial_handoff_status === "rejected_no_financial_effect" ? "رفض دون أثر مالي حسب Backend" : "الأثر المالي غير متاح" };
    if (row.status === "approved") return { label: "معتمد", effect: row.financial_handoff_status === "posted" && row.financial_txn_group_id ? `قيد Backend: ${row.financial_txn_group_id}` : "نتيجة Backend المالية غير متاحة" };
    return { label: "غير متاح", effect: "BLOCKED_BY_BACKEND / not_ready" };
}
