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
        required_due_at_required: "حدد تاريخًا ووقتًا للموعد الإجباري.",
    };
    return messages[detail?.code] || error?.message || fallback;
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
