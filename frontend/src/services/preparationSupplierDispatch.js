import api from "../lib/api";

const ERROR_LABELS = {
    preparation_manage_permission_required: "تحتاج صلاحية إدارة التجهيز لفتح ملفاتك.",
    preparation_file_not_found: "ملف التجهيز غير موجود أو لم يكتمل تسجيله.",
    piece_quantity_exceeds_available: "الكمية المختارة لم تعد متاحة؛ حدّث الملف.",
    ambiguous_piece_group: "تغيّر تجميع القطع بعد فتح الصفحة؛ حدّث الملف ثم أعد الإرسال.",
    duplicate_piece_group: "يوجد تكرار غير متوقع في مجموعة القطع؛ حدّث الملف ثم أعد الإرسال.",
    invalid_piece_selection: "تعذّر مطابقة القطع المحددة مع الملف الحالي؛ حدّث الصفحة.",
    supplier_dispatch_supplier_not_found: "المورد غير موجود أو موقوف.",
    supplier_dispatch_service_mismatch: "المورد المحدد لا يقدم خدمة متبقية لهذا المنتج.",
    supplier_dispatch_piece_conflict: "تغيّرت إحدى القطع أثناء الرفع؛ حدّث الملف.",
    preparation_rejection_piece_conflict: "تغيّرت إحدى القطع أثناء الرفض؛ حدّث الملف.",
    preparation_reassignment_piece_conflict: "أُسندت إحدى القطع من مدير آخر؛ حدّث القائمة.",
    responsible_employee_unavailable: "اختر موظفًا نشطًا يملك صلاحية إدارة التجهيز.",
    supplier_dispatch_not_found: "دفعة المورد غير موجودة أو تغيرت حالتها.",
    supplier_dispatch_owner_required: "تأكيد الجاهزية متاح للموظف الذي رفع الدفعة أو المدير.",
};

function dispatchError(error, fallback) {
    const detail = error?.response?.data?.detail;
    const wrapped = new Error(
        detail?.message
        || ERROR_LABELS[detail?.code]
        || detail?.code
        || error?.message
        || fallback,
    );
    wrapped.code = detail?.code;
    wrapped.forbidden = error?.response?.status === 403;
    return wrapped;
}

export function newPreparationDispatchRequestId(prefix = "supplier-dispatch") {
    if (globalThis.crypto?.randomUUID) {
        return `${prefix}:${globalThis.crypto.randomUUID()}`;
    }
    return `${prefix}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

export function normalizeSupplierDispatchPayload(payload = {}) {
    const files = Array.isArray(payload?.files)
        ? payload.files.filter((file) => file && String(file.file_number || "").trim())
        : [];
    if (files.length !== 1) return payload;

    const [file] = files;
    return {
        client_request_id: payload.client_request_id,
        supplier_id: payload.supplier_id,
        note: payload.note ?? null,
        file_number: String(file.file_number || "").trim(),
        selections: Array.isArray(file.selections) ? file.selections : [],
    };
}

export async function getPreparationSupplierWorkspace({ limit = 100 } = {}) {
    try {
        return (await api.get("/supplier-dispatch-v1/workspace", {
            params: { limit },
        })).data;
    } catch (error) {
        throw dispatchError(error, "تعذّر تحميل ملفات الموظف وحسابات الموردين.");
    }
}

export async function sendPreparationPiecesToSupplier(payload) {
    try {
        const requestPayload = normalizeSupplierDispatchPayload(payload);
        return (await api.post("/supplier-dispatch-v1/dispatches", requestPayload)).data;
    } catch (error) {
        throw dispatchError(error, "تعذّر رفع المنتجات إلى المورد.");
    }
}

export async function rejectPreparationPieces(payload) {
    try {
        return (await api.post("/supplier-dispatch-v1/rejections", payload)).data;
    } catch (error) {
        throw dispatchError(error, "تعذّر نقل المنتجات إلى غير المسندة.");
    }
}

export async function getUnassignedPreparationPieces() {
    try {
        return (await api.get("/supplier-dispatch-v1/manager/unassigned")).data;
    } catch (error) {
        throw dispatchError(error, "تعذّر تحميل المنتجات غير المسندة.");
    }
}

export async function reassignPreparationPieces(payload) {
    try {
        return (await api.post("/supplier-dispatch-v1/manager/reassign", payload)).data;
    } catch (error) {
        throw dispatchError(error, "تعذّر إعادة إسناد المنتجات.");
    }
}

export async function markSupplierDispatchReady(dispatchId, note = "") {
    try {
        return (await api.post(
            `/supplier-dispatch-v1/dispatches/${encodeURIComponent(dispatchId)}/ready`,
            { note: String(note || "").trim() || null },
        )).data;
    } catch (error) {
        throw dispatchError(error, "تعذّر تأكيد جاهزية دفعة المورد.");
    }
}
