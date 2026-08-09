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
    supplier_receiving_quantity_exceeds_available: "الكمية المختارة أكبر من الكمية المتبقية لهذا المنتج.",
    supplier_receiving_supplier_not_found: "المورد غير موجود أو غير نشط.",
    supplier_receiving_supplier_services_required: "اربط المورد بخدمة واحدة على الأقل من صفحة موردي ميزان 2.",
    supplier_piece_barcode_invalid: "الباركود غير صالح. امسح QR الموجود على بطاقة القطعة.",
    supplier_piece_barcode_not_found: "لم نعثر على قطعة بهذا الباركود.",
    supplier_piece_already_received: "تم استلام هذه القطعة سابقًا؛ لم تُسجّل مرة ثانية.",
    supplier_piece_blocked: "القطعة متوقفة ولا يمكن استلامها.",
    supplier_piece_cancelled: "القطعة ملغاة ولا يمكن استلامها.",
    supplier_piece_not_started: "ابدأ ملف التجهيز أولًا قبل استلام القطعة.",
    supplier_piece_services_missing: "القطعة لا تحتوي على خدمة تجهيز. اربط المنتج بخدمة من مكونات المنتجات أولًا.",
    supplier_piece_service_mismatch: "المورد المحدد لا يقدم أي خدمة متبقية مرتبطة بهذه القطعة.",
    supplier_piece_already_in_receiving_session: "هذه القطعة مضافة بالفعل إلى جلسة استلام مفتوحة.",
    supplier_piece_not_dispatched: "أرسل القطعة إلى المورد قبل استلامها.",
    supplier_piece_dispatched_to_different_supplier: "هذه القطعة مرسلة إلى مورد مختلف عن جلسة الاستلام الحالية.",
    supplier_receiving_invoice_duplicate_piece: "تحتوي مسودة الفاتورة على قطعة مكررة.",
    supplier_receiving_invoice_duplicate_service: "تحتوي مسودة الفاتورة على خدمة مكررة.",
    supplier_receiving_invoice_piece_mismatch: "تغيّرت قطع الجلسة. حدّث الفاتورة ثم احفظ من جديد.",
    supplier_receiving_invoice_group_mismatch: "لا يمكن جمع منتجات أو خدمات مختلفة في سطر فاتورة واحد.",
    supplier_receiving_service_not_on_product: "الخدمة المختارة غير مرتبطة بالمنتج. استخدم صلاحية إضافة خدمة للمنتج.",
    supplier_receiving_service_not_available: "الخدمة غير متاحة لهذا المورد.",
    supplier_receiving_service_option_conflict: "الخدمة مرتبطة بخيار محدد في المنتج ولا يمكن إضافتها لكل المنتج.",
    supplier_receiving_service_add_permission_required: "لا تملك صلاحية إضافة خدمة للمنتج.",
    supplier_receiving_price_permission_required: "لا تملك صلاحية تعديل هذا السعر.",
    supplier_receiving_product_already_charged: "سبق احتساب سعر المنتج؛ الفاتورة الجديدة تقبل الخدمات المتبقية فقط.",
    supplier_receiving_conflicting_service_prices: "لا يمكن حفظ سعرين مختلفين للخدمة المشتركة نفسها في فاتورة واحدة.",
    supplier_receiving_conflicting_product_prices: "لا يمكن حفظ سعرين مختلفين للمنتج أو الخيار نفسه في فاتورة واحدة.",
    supplier_receiving_invoice_total_required: "إجمالي فاتورة المورد يجب أن يكون أكبر من صفر.",
    supplier_receiving_atomic_transaction_required: "قاعدة البيانات لا تدعم الحفظ المحاسبي الذري؛ بقيت الجلسة مفتوحة.",
    supplier_receiving_accounting_transaction_failed: "تعذّر اعتماد الفاتورة محاسبيًا؛ بقيت الجلسة مفتوحة ولم تُنشأ مديونية.",
    supplier_receiving_invoice_event_conflict: "تغيّر سجل إحدى القطع أثناء الاعتماد؛ بقيت الجلسة مفتوحة.",
    supplier_invoice_not_found: "فاتورة المورد غير موجودة أو لا تملك صلاحية عرضها.",
    supplier_invoice_share_evidence_image_required: "إثبات المشاركة يجب أن يكون صورة من المحادثة.",
    supplier_invoice_share_evidence_image_invalid: "ملف إثبات المشاركة ليس صورة صالحة.",
    supplier_invoice_share_evidence_size_invalid: "صورة إثبات المشاركة فارغة أو أكبر من 5 ميجابايت.",
    supplier_invoice_share_evidence_required: "التقط صورة محادثة المورد وارفعها قبل تأكيد المشاركة.",
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

export async function scanSupplierReceivingPiece(sessionId, barcode, { quantity = null } = {}) {
    try {
        return (await api.post(
            `/supplier-receiving-v1/sessions/${encodeURIComponent(sessionId)}/scan`,
            { barcode, ...(quantity == null ? {} : { quantity }) },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر استلام القطعة.");
    }
}

export async function getSupplierReceivingInvoice(invoiceId) {
    try {
        return (await api.get(
            `/supplier-receiving-v1/invoices/${encodeURIComponent(invoiceId)}`,
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر تحميل فاتورة المورد.");
    }
}

export async function downloadSupplierReceivingInvoicePdf(invoiceId) {
    try {
        return (await api.get(
            `/supplier-receiving-v1/invoices/${encodeURIComponent(invoiceId)}/pdf`,
            { responseType: "blob" },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر تجهيز ملف فاتورة المورد.");
    }
}

export async function uploadSupplierInvoiceShareEvidence(invoiceId, file) {
    const form = new FormData();
    form.append("file", file);
    try {
        return (await api.post(
            `/supplier-receiving-v1/invoices/${encodeURIComponent(invoiceId)}/share-evidence`,
            form,
            { headers: { "Content-Type": "multipart/form-data" } },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر رفع صورة محادثة المورد.");
    }
}

export async function confirmSupplierInvoiceShare(invoiceId, { note = "" } = {}) {
    try {
        return (await api.post(
            `/supplier-receiving-v1/invoices/${encodeURIComponent(invoiceId)}/confirm-share`,
            { note: note || null },
        )).data;
    } catch (error) {
        throw receivingError(error, "تعذّر تأكيد مشاركة فاتورة المورد.");
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
