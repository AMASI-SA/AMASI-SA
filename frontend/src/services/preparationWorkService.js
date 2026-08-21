import api from "../lib/api";

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail?.message) return detail.message;
    const messages = {
        preparation_manage_permission_required: "تحتاج صلاحية إدارة التجهيز لفتح منتجاتك.",
        assigned_file_start_permission_required: "بدء الملف متاح للموظف المسند أو مدير التشغيل فقط.",
        preparation_file_not_found: "ملف التجهيز غير موجود أو لم يكتمل تسجيله.",
        preparation_file_start_conflict: "بدأ موظف آخر هذا الملف أو تغيرت حالته. حدّث الصفحة.",
        preparation_file_pieces_missing: "لم تكتمل سجلات قطع الملف. حدّث الصفحة ثم أعد المحاولة.",
        preparation_file_start_requires_full_assignment: "لا يمكن بدء التنفيذ قبل إسناد جميع القطع المتبقية لطلبات هذا الملف.",
        required_due_at_required: "حدد تاريخًا ووقتًا للموعد الإجباري.",
        preparation_receipt_permission_required: "تحتاج صلاحية الاستلام من التجهيز لفتح هذه الصفحة.",
        preparation_piece_not_found: "لم نتعرف على هذا الباركود. تأكد أنه باركود قطعة من ميزان.",
        preparation_receipt_order_not_found: "لم نجد طلب تجهيز بهذا الرقم.",
        preparation_receipt_search_required: "اكتب رقم الطلب أو صوّر باركود القطعة.",
        preparation_piece_already_received: "تم استلام هذه القطعة مسبقًا.",
        preparation_piece_cancelled: "هذه القطعة ملغاة ولا يمكن استلامها.",
        preparation_piece_stopped: "هذه القطعة متوقفة. عالج سبب التوقف أولًا.",
        preparation_piece_employee_required: "يجب إسناد القطعة إلى موظف تجهيز أولًا.",
        preparation_piece_supplier_receiving_in_progress: "القطعة داخل جلسة استلام من المورد الآن.",
        preparation_piece_supplier_receipt_required: "استلم القطعة من المورد أولًا ثم استلمها من التجهيز.",
        preparation_piece_services_incomplete: "لا يمكن استلام القطعة؛ توجد خدمة مطلوبة لم تُنفذ بعد. أكمل جميع الخدمات ثم أعد تصوير الباركود.",
        preparation_piece_not_started: "لم يبدأ موظف التجهيز هذه القطعة بعد.",
        preparation_piece_not_ready_for_receipt: "حالة القطعة لا تسمح باستلامها الآن.",
        preparation_piece_receipt_conflict: "تغيرت حالة القطعة. ابحث عن الطلب مرة أخرى.",
        assembly_piece_not_found: "لم نتعرف على هذا الباركود داخل التجميع والعنونة.",
        assembly_search_required: "اكتب رقم الطلب أو صوّر باركود المنتج.",
        assembly_order_not_ready: "هذا الطلب غير جاهز للتجميع والعنونة بعد.",
        assembly_order_products_not_found: "لم نجد منتجات هذا الطلب داخل التجميع والعنونة.",
        assembly_piece_preparation_receipt_required: "استلم المنتج من موظف التجهيز أولًا.",
        assembly_piece_stopped: "هذا المنتج متوقف ولا يمكن إكماله.",
        assembly_piece_ready_conflict: "تغيرت حالة المنتج. افتح الطلب مرة أخرى.",
    };
    const base = messages[detail?.code] || error?.message || fallback;
    const pendingNames = Array.isArray(detail?.pending_service_names)
        ? detail.pending_service_names.filter(Boolean)
        : [];
    if (
        detail?.code === "preparation_piece_services_incomplete"
        && pendingNames.length
    ) {
        return `${base} الخدمات غير المنجزة: ${pendingNames.join("، ")}.`;
    }
    return base;
}

export async function getMyPreparationWork({ limit = 50 } = {}) {
    try {
        return (await api.get("/preparation-work-v1/my-work", {
            params: { limit },
        })).data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل المنتجات المسندة إليك."));
    }
}

export async function getPreparationManagerSummary({ date } = {}) {
    try {
        const params = date ? { date } : {};
        return (await api.get("/preparation-work-v1/manager/summary", { params })).data;
    } catch (error) {
        const forbidden = error?.response?.status === 403;
        const wrapped = new Error(errorMessage(error, "تعذّر تحميل إدارة منتجات الموظفين."));
        wrapped.forbidden = forbidden;
        throw wrapped;
    }
}

export async function startPreparationFile(fileNumber, note = "") {
    try {
        return (await api.post(
            `/preparation-work-v1/files/${encodeURIComponent(fileNumber)}/start`,
            { note: String(note || "").trim() || null },
        )).data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر بدء تنفيذ ملف التجهيز."));
    }
}

export async function updatePreparationFileSchedule(
    fileNumber,
    { mode, requiredDueAt = null },
) {
    try {
        return (await api.put(
            `/preparation-work-v1/files/${encodeURIComponent(fileNumber)}/schedule`,
            {
                mode,
                required_due_at: requiredDueAt || null,
            },
        )).data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحديث موعد ملف التجهيز."));
    }
}

export function newPreparationReceiptRequestId() {
    if (globalThis.crypto?.randomUUID) {
        return `preparation-receipt:${globalThis.crypto.randomUUID()}`;
    }
    return `preparation-receipt:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

export async function searchPreparationReceipt(query) {
    try {
        return (await api.get("/preparation-work-v1/receiving/search", {
            params: { q: String(query || "").trim() },
        })).data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر البحث عن طلب التجهيز."));
    }
}

export async function receivePreparationPiece(pieceId, clientRequestId) {
    try {
        return (await api.post(
            `/preparation-work-v1/receiving/pieces/${encodeURIComponent(pieceId)}/receive`,
            { client_request_id: clientRequestId || newPreparationReceiptRequestId() },
        )).data;
    } catch (error) {
        const wrapped = new Error(errorMessage(error, "تعذّر استلام المنتج من التجهيز."));
        wrapped.code = error?.response?.data?.detail?.code;
        wrapped.detail = error?.response?.data?.detail;
        throw wrapped;
    }
}

export function newAssemblyReadyRequestId() {
    if (globalThis.crypto?.randomUUID) {
        return `assembly-ready:${globalThis.crypto.randomUUID()}`;
    }
    return `assembly-ready:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

export async function searchAssemblyOrder(query) {
    try {
        return (await api.get("/preparation-work-v1/assembly/search", {
            params: { q: String(query || "").trim() },
        })).data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر البحث داخل التجميع والعنونة."));
    }
}

export async function markAssemblyPieceReady(pieceId, clientRequestId) {
    try {
        return (await api.post(
            `/preparation-work-v1/assembly/pieces/${encodeURIComponent(pieceId)}/ready`,
            { client_request_id: clientRequestId || newAssemblyReadyRequestId() },
        )).data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تسجيل المنتج جاهزًا."));
    }
}
