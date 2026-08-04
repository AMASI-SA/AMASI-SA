import api from "../lib/api";

const ERROR_LABELS = {
    supplier_receiving_session_not_found: "جلسة الاستلام غير موجودة.",
    supplier_receiving_session_owner_required: "هذه الجلسة مفتوحة باسم موظف آخر.",
    supplier_receiving_session_closed: "الجلسة مغلقة ولا تقبل قطعًا جديدة.",
    supplier_receiving_open_session_exists: "لديك جلسة استلام مفتوحة بالفعل.",
    supplier_receiving_scan_busy: "يوجد مسح جارٍ في الجلسة. انتظر لحظة ثم أعد المحاولة.",
    supplier_receiving_supplier_not_found: "المورد غير موجود أو غير نشط.",
    supplier_piece_barcode_invalid: "الباركود غير صالح. امسح QR الموجود على بطاقة القطعة.",
    supplier_piece_barcode_not_found: "لم نعثر على قطعة بهذا الباركود.",
    supplier_piece_already_received: "تم استلام هذه القطعة سابقًا؛ لم تُسجّل مرة ثانية.",
    supplier_piece_blocked: "القطعة متوقفة ولا يمكن استلامها.",
    supplier_piece_cancelled: "القطعة ملغاة ولا يمكن استلامها.",
    supplier_piece_not_started: "ابدأ ملف التجهيز أولًا قبل استلام القطعة.",
    legacy_order_barcode_ambiguous: "باركود الطلب القديم يطابق أكثر من قطعة. أعد تنزيل ملف التجهيز لطباعته بباركود القطعة الفريد.",
    fulfillment_permission_required: "تحتاج صلاحية استلام منتجات التجهيز.",
};

function receivingError(error, fallback) {
    const detail = error?.response?.data?.detail;
    const result = new Error(
        detail?.message
        || ERROR_LABELS[detail?.code]
        || detail?.code
        || error?.message
        || fallback,
    );
    result.code = detail?.code;
    result.detail = detail;
    return result;
}

export async function loadSupplierReceivingCatalog({ limit = 50 } = {}) {
    try {
        return (await api.get("/supplier-receiving-v1/catalog", {
            params: { limit },
        })).data;
    } catch (error) {
        throw receivingError(error, "تعذّر تحميل جلسات استلام المورد.");
    }
}

export async function openSupplierReceivingSession(payload) {
    try {
        return (await api.post("/supplier-receiving-v1/sessions", payload)).data;
    } catch (error) {
        throw receivingError(error, "تعذّر فتح جلسة الاستلام.");
    }
}

export async function scanSupplierReceivingPiece(sessionId, barcode) {
    try {
        return (await api.post(
            `/supplier-receiving-v1/sessions/${encodeURIComponent(sessionId)}/scan`,
            { barcode },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر استلام القطعة.");
    }
}

export async function closeSupplierReceivingSession(sessionId, note = "") {
    try {
        return (await api.post(
            `/supplier-receiving-v1/sessions/${encodeURIComponent(sessionId)}/close`,
            { note: note || null },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر إغلاق جلسة الاستلام.");
    }
}

export function newSupplierReceivingRequestId() {
    if (globalThis.crypto?.randomUUID) {
        return `supplier-receiving:${globalThis.crypto.randomUUID()}`;
    }
    return `supplier-receiving:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}
