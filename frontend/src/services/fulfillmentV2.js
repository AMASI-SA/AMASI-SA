import api from "../lib/api";

function detailMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (detail?.code === "store_courier_label_barcode_mismatch") {
        const scanned = detail?.scanned_order_number;
        const expected = detail?.expected_order_number;
        if (scanned && expected) {
            return `هذه البوليصة تخص الطلب رقم ${scanned} ولا تخص الطلب رقم ${expected}.`;
        }
    }
    const labels = {
        ready_orders_changed_refresh_required: "تغيّرت قائمة الطلبات الجاهزة. حدّث الصفحة ثم أعد الاختيار.",
        ready_orders_claim_conflict: "استلم موظف آخر أحد الطلبات في اللحظة نفسها. حدّث القائمة.",
        batch_already_printed_reprint_reason_required: "هذه الدفعة طُبعت سابقًا. أدخل سبب إعادة الطباعة.",
        batch_must_be_printed_before_packing: "اطبع ملف الدفعة قبل تأكيد التغليف.",
        batch_must_be_packed_before_handoff: "أكد التغليف قبل التسليم لشركة الشحن.",
        fulfillment_responsibility_required: "هذا الموظف غير معيّن لهذه المسؤولية التشغيلية.",
        fulfillment_permission_required: "لا تملك الصلاحية المطلوبة لهذه العملية.",
        carrier_label_barcode_required: "صوّر باركود بوليصة الشحن أولًا.",
        carrier_label_barcode_mismatch: "هذه ليست بوليصة الشحن الخاصة بهذا الطلب.",
        carrier_label_not_ready: "انتظر حتى تصبح بوليصة شركة الشحن جاهزة.",
        carrier_tracking_number_missing: "رقم تتبع البوليصة غير محفوظ؛ أعد التحقق من سلة.",
        carrier_shipment_not_confirmed_by_labeling: "هذه الشحنة لم يؤكد موظف العنونة طباعتها، أو أن الباركود غير صحيح.",
        carrier_shipment_already_received: detail?.employee_name
            ? `أُضيفت الشحنة مسبقًا إلى حساب ${detail.employee_name}.`
            : "أُضيفت هذه الشحنة مسبقًا إلى حساب موظف تسليم الشحن.",
        carrier_shipment_no_longer_waiting: "هذه الشحنة لم تعد بانتظار التسليم لشركة الشحن.",
        store_courier_separate_flow: "طلبات مندوب المتجر لها مسار تسليم مستقل.",
        store_courier_label_barcode_mismatch: "رمز بوليصة مندوب المتجر لا يطابق رقم الطلب.",
    };
    return labels[detail?.code] || detail?.message || detail?.code || error?.message || fallback;
}

function fulfillmentError(error, fallback) {
    const converted = new Error(detailMessage(error, fallback));
    const detail = error?.response?.data?.detail;
    converted.code = detail?.code || "";
    converted.details = detail && typeof detail === "object" ? { ...detail } : {};
    converted.status = error?.response?.status || null;
    return converted;
}
export async function listReadyToShipOrders({ limit = 100 } = {}) {
    try {
        return (await api.get("/fulfillment-v2/ready-to-ship", { params: { limit } })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل الطلبات الجاهزة للشحن."));
    }
}

export async function listCompletedFulfillmentOrders({ limit = 100 } = {}) {
    try {
        return (await api.get("/fulfillment-v2/completed", { params: { limit } })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل الطلبات المكتملة."));
    }
}

export async function issueCompletedOrderCarrierLabel(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    try {
        return (await api.post(
            `/fulfillment-v2/completed/${encodeURIComponent(normalized)}/carrier-label`,
        )).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحويل الطلب في سلة وإصدار البوليصة."));
    }
}

export async function refreshCompletedOrderCarrierLabel(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    try {
        return (await api.post(
            `/fulfillment-v2/completed/${encodeURIComponent(normalized)}/carrier-label/refresh`,
        )).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر التحقق من رابط البوليصة في سلة."));
    }
}

export async function confirmCompletedCarrierLabelPrint(orderNumber, barcode) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    try {
        return (await api.post(
            `/fulfillment-v2/completed/${encodeURIComponent(normalized)}/carrier-label/confirm-print`,
            { barcode: String(barcode || "").trim() },
        )).data;
    } catch (error) {
        throw fulfillmentError(error, "تعذر تأكيد طباعة بوليصة الشحن.");
    }
}

export async function listCarrierHandoffShipments({ limit = 100 } = {}) {
    try {
        return (await api.get("/fulfillment-v2/carrier-handoff", { params: { limit } })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل شحنات موظف التسليم."));
    }
}

export async function scanCarrierHandoffShipment(barcode) {
    try {
        return (await api.post("/fulfillment-v2/carrier-handoff/scan", {
            barcode: String(barcode || "").trim(),
        })).data;
    } catch (error) {
        throw fulfillmentError(error, "تعذر تسجيل الشحنة في حساب موظف التسليم.");
    }
}

export async function listDeliveryTrackingShipments({ stage, limit = 100 } = {}) {
    const normalizedStage = stage === "delivered" ? "delivered" : "delivering";
    try {
        return (await api.get("/fulfillment-v2/delivery-tracking", {
            params: { stage: normalizedStage, limit },
        })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل طلبات التوصيل."));
    }
}

export async function claimReadyToShipBatch(orderNumbers) {
    try {
        return (await api.post("/fulfillment-v2/ready-to-ship/claim", { order_numbers: orderNumbers })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر استلام دفعة الطلبات."));
    }
}

export async function listFulfillmentBatches({ limit = 50 } = {}) {
    try {
        return (await api.get("/fulfillment-v2/batches", { params: { limit } })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل دفعات الشحن."));
    }
}

function downloadPdf(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

export async function printFulfillmentBatch(batchId, reprintReason = "") {
    try {
        const response = await api.post(
            `/fulfillment-v2/batches/${encodeURIComponent(batchId)}/print`,
            { reprint_reason: reprintReason || null },
            { responseType: "blob" },
        );
        downloadPdf(response.data, `mezan_shipping_batch_${batchId}.pdf`);
        return {
            ok: true,
            reprint: response.headers?.["x-mezan-reprint"] === "true",
        };
    } catch (error) {
        if (error?.response?.data instanceof Blob) {
            try {
                const parsed = JSON.parse(await error.response.data.text());
                error.response.data = parsed;
            } catch {
                // Keep the normal fallback message for a non-JSON provider error.
            }
        }
        throw new Error(detailMessage(error, "تعذر إنشاء ملف الطباعة."));
    }
}

export async function confirmFulfillmentBatchPacked(batchId, note = "") {
    try {
        return (await api.post(`/fulfillment-v2/batches/${encodeURIComponent(batchId)}/pack`, { note: note || null })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تأكيد التغليف."));
    }
}

export async function confirmFulfillmentBatchHandoff(batchId, note = "") {
    try {
        return (await api.post(`/fulfillment-v2/batches/${encodeURIComponent(batchId)}/handoff`, { note: note || null })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تأكيد التسليم لشركة الشحن."));
    }
}
