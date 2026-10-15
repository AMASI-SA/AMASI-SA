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
    fulfillment_experiment_owner_required: "إعادة الطلب للتجربة متاحة لمالك المتجر فقط.",
    fulfillment_experiment_confirmation_required: "تعذر تأكيد رقم الطلب لإعادة التجربة.",
    fulfillment_experiment_order_not_found: "لم يُعثر على مسار تجهيز لهذا الطلب.",
    fulfillment_experiment_open_receiving_session: "أغلق أو ألغِ جلسة استلام المورد المفتوحة لهذا الطلب أولًا.",
    fulfillment_experiment_active_hold_must_release: "أنهِ الإيقافات النشطة قبل إعادة الطلب للتجربة.",
    fulfillment_experiment_atomic_transaction_required: "قاعدة البيانات لا تدعم إعادة الطلب الذرية؛ لم تتغير أي مرحلة.",
    fulfillment_experiment_piece_conflict: "تغيّرت قطعة أثناء إعادة الطلب؛ لم تتغير أي مرحلة.",
    fulfillment_experiment_allocation_conflict: "تغيّر تخصيص أثناء إعادة الطلب؛ لم تتغير أي مرحلة.",
    fulfillment_experiment_workflow_conflict: "تغيّرت مرحلة الطلب أثناء الإعادة؛ حدّث الصفحة وحاول مجددًا.",
    fulfillment_experiment_reset_failed: "تعذّرت إعادة الطلب ذريًا؛ لم تتغير أي مرحلة.",
};

function experimentError(error, fallback) {
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

export async function getFulfillmentExperimentState(orderNumber) {
    try {
        return (await api.get(
            `/fulfillment-experiments-v1/orders/${encodeURIComponent(orderNumber)}`,
        )).data;
    } catch (error) {
        throw experimentError(error, "تعذّر تحميل تحكم التجهيز للطلب.");
    }
}

export async function resetFulfillmentExperiment(
    orderNumber,
    note = "",
    deliveryFlow = "salla",
) {
    const normalizedFlow = deliveryFlow === "store_courier"
        ? "store_courier"
        : "salla";
    try {
        return (await api.post(
            `/fulfillment-experiments-v1/orders/${encodeURIComponent(orderNumber)}/reset`,
            {
                confirmation: `RESET ${String(orderNumber || "").trim()}`,
                note: String(note || "").trim() || null,
                delivery_flow: normalizedFlow,
            },
        )).data;
    } catch (error) {
        throw experimentError(error, "تعذّرت إعادة الطلب للتجربة.");
    }
}

export async function createFulfillmentHold(orderNumber, payload) {
    try {
        return (await api.post(
            `/fulfillment-experiments-v1/orders/${encodeURIComponent(orderNumber)}/holds`,
            payload,
        )).data;
    } catch (error) {
        throw experimentError(error, "تعذّر إيقاف التجهيز.");
    }
}

export async function releaseFulfillmentHold(holdId, note = "") {
    try {
        return (await api.post(
            `/fulfillment-experiments-v1/holds/${encodeURIComponent(holdId)}/release`,
            { note: String(note || "").trim() || null },
        )).data;
    } catch (error) {
        throw experimentError(error, "تعذّر إنهاء الإيقاف.");
    }
}
