import api from "../lib/api";

const ERROR_LABELS = {
    fulfillment_stop_permission_required: "لا تملك صلاحية إيقاف التجهيز.",
    fulfillment_self_stop_piece_only: "موظف التجهيز يستطيع إيقاف قطعة مسندة إليه فقط.",
    fulfillment_stop_manage_permission_required: "أنواع إيقاف خدمة العملاء تحتاج صلاحية إدارة الإيقافات.",
    fulfillment_stop_target_not_available: "لا توجد قطع متاحة ضمن النطاق المحدد.",
    fulfillment_stop_already_active: "يوجد إيقاف نشط على قطعة من هذا النطاق.",
    fulfillment_stop_piece_conflict: "تغيّرت حالة إحدى القطع. حدّث الطلب وأعد المحاولة.",
    fulfillment_stop_persist_failed: "تعذّر حفظ الإيقاف كاملًا؛ أُعيدت القطع إلى حالتها السابقة.",
    fulfillment_hold_not_found: "سجل الإيقاف غير موجود.",
    fulfillment_hold_not_active: "الإيقاف لم يعد نشطًا.",
    fulfillment_hold_release_conflict: "تغيّر سجل الإيقاف أثناء الاستئناف؛ بقيت القطع متوقفة.",
    fulfillment_hold_release_permission_required: "لا تملك صلاحية إنهاء هذا الإيقاف.",
};

function holdError(error, fallback) {
    const detail = error?.response?.data?.detail;
    const result = new Error(
        detail?.message
        || ERROR_LABELS[detail?.code]
        || detail?.code
        || error?.message
        || fallback,
    );
    result.code = detail?.code;
    result.status = error?.response?.status;
    result.detail = detail;
    return result;
}

export async function createFulfillmentHold(orderNumber, payload) {
    try {
        return (await api.post(
            `/fulfillment-experiments-v1/orders/${encodeURIComponent(orderNumber)}/holds`,
            payload,
        )).data;
    } catch (error) {
        throw holdError(error, "تعذّر إيقاف التجهيز.");
    }
}

export async function releaseFulfillmentHold(holdId, note = "") {
    try {
        return (await api.post(
            `/fulfillment-experiments-v1/holds/${encodeURIComponent(holdId)}/release`,
            { note: String(note || "").trim() || null },
        )).data;
    } catch (error) {
        throw holdError(error, "تعذّر إنهاء الإيقاف.");
    }
}
