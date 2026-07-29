import api from "../lib/api";

function detailMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    const labels = {
        ready_orders_changed_refresh_required: "تغيّرت قائمة الطلبات الجاهزة. حدّث الصفحة ثم أعد الاختيار.",
        ready_orders_claim_conflict: "استلم موظف آخر أحد الطلبات في اللحظة نفسها. حدّث القائمة.",
        batch_already_printed_reprint_reason_required: "هذه الدفعة طُبعت سابقًا. أدخل سبب إعادة الطباعة.",
        batch_must_be_printed_before_packing: "اطبع ملف الدفعة قبل تأكيد التغليف.",
        batch_must_be_packed_before_handoff: "أكد التغليف قبل التسليم لشركة الشحن.",
        fulfillment_responsibility_required: "هذا الموظف غير معيّن لهذه المسؤولية التشغيلية.",
        fulfillment_permission_required: "لا تملك الصلاحية المطلوبة لهذه العملية.",
    };
    return labels[detail?.code] || detail?.message || detail?.code || error?.message || fallback;
}
export async function listReadyToShipOrders({ limit = 100 } = {}) {
    try {
        return (await api.get("/fulfillment-v2/ready-to-ship", { params: { limit } })).data;
    } catch (error) {
        throw new Error(detailMessage(error, "تعذر تحميل الطلبات الجاهزة للشحن."));
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
