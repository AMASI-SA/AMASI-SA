import api from "../lib/api";

const ERROR_LABELS = {
    supplier_receiving_session_not_found: "جلسة الاستلام غير موجودة.",
    supplier_receiving_session_owner_required: "هذه الجلسة مفتوحة باسم موظف آخر.",
    supplier_receiving_session_closed: "الجلسة مغلقة ولا تقبل قطعًا جديدة.",
    supplier_receiving_session_not_open: "هذه الجلسة لم تعد مفتوحة.",
    supplier_receiving_session_cancel_conflict: "تغيّرت الجلسة أثناء الإلغاء. حدّث الصفحة وحاول من جديد.",
    supplier_receiving_cancel_piece_conflict: "تغيّرت إحدى القطع أثناء الإلغاء. حدّث الصفحة وحاول من جديد.",
    supplier_receiving_cancel_rollback_unavailable: "لا يمكن إلغاء جلسة قديمة تحتوي قطعًا مستلمة بأمان. احفظها أو تواصل مع المسؤول.",
    supplier_receiving_open_session_exists: "لديك جلسة استلام مفتوحة بالفعل.",
    supplier_receiving_scan_busy: "يوجد مسح جارٍ في الجلسة. انتظر لحظة ثم أعد المحاولة.",
    supplier_receiving_supplier_not_found: "المورد غير موجود أو غير نشط.",
    supplier_receiving_supplier_services_required: "اربط المورد بخدمة واحدة على الأقل من صفحة موردي ميزان 2.",
    supplier_piece_barcode_invalid: "الباركود غير صالح. امسح QR الموجود على بطاقة القطعة.",
    supplier_piece_barcode_not_found: "لم نعثر على قطعة بهذا الباركود.",
    supplier_piece_already_received: "تم استلام هذه القطعة سابقًا؛ لم تُسجّل مرة ثانية.",
    supplier_piece_blocked: "القطعة متوقفة ولا يمكن استلامها.",
    supplier_piece_cancelled: "القطعة ملغاة ولا يمكن استلامها.",
    supplier_piece_not_started: "ابدأ ملف التجهيز أولًا قبل استلام القطعة.",
    supplier_piece_services_missing: "القطعة لا تحتوي على خدمة تجهيز. اربط المنتج بخدمة من مكونات المنتجات أولًا.",
    supplier_piece_service_mismatch: "المورد المحدد لا يقدم جميع الخدمات المطلوبة لهذه القطعة.",
    supplier_receiving_invoice_duplicate_piece: "تحتوي مسودة الفاتورة على قطعة مكررة.",
    supplier_receiving_invoice_piece_mismatch: "تغيّرت قطع الجلسة. حدّث الفاتورة ثم احفظ من جديد.",
    supplier_receiving_invoice_group_mismatch: "لا يمكن جمع منتجات أو خدمات مختلفة في سطر فاتورة واحد.",
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

export async function closeSupplierReceivingSession(sessionId, { note = "", invoice_lines = [] } = {}) {
    try {
        return (await api.post(
            `/supplier-receiving-v1/sessions/${encodeURIComponent(sessionId)}/close`,
            { note: note || null, invoice_lines },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر إغلاق جلسة الاستلام.");
    }
}

export async function cancelSupplierReceivingSession(sessionId, { note = "" } = {}) {
    try {
        return (await api.post(
            `/supplier-receiving-v1/sessions/${encodeURIComponent(sessionId)}/cancel`,
            { note: note || null },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر إلغاء جلسة الاستلام.");
    }
}

export function newSupplierReceivingRequestId() {
    if (globalThis.crypto?.randomUUID) {
        return `supplier-receiving:${globalThis.crypto.randomUUID()}`;
    }
    return `supplier-receiving:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}
