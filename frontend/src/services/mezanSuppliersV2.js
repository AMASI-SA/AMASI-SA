import api from "../lib/api";

const ERROR_LABELS = {
    supplier_company_name_exists: "يوجد مورد في ميزان 2 بالاسم نفسه.",
    supplier_service_required: "اختر خدمة واحدة على الأقل يقدمها المورد.",
    supplier_service_not_found: "إحدى الخدمات المحددة لم تعد موجودة في كتالوج الخدمات.",
    mezan_supplier_not_found: "المورد غير موجود في ميزان 2.",
    fulfillment_permission_required: "لا تملك صلاحية إدارة موردي ميزان 2.",
};

function supplierError(error, fallback) {
    const detail = error?.response?.data?.detail;
    const validation = Array.isArray(detail)
        ? detail.find((row) => String(row?.msg || "").includes("supplier_service_required"))
        : null;
    const code = detail?.code || (validation ? "supplier_service_required" : "");
    const result = new Error(
        detail?.message
        || ERROR_LABELS[code]
        || code
        || error?.message
        || fallback,
    );
    result.code = code;
    result.detail = detail;
    return result;
}

export async function loadMezanSuppliersWorkspace() {
    try {
        return (await api.get("/suppliers-v2/workspace")).data;
    } catch (error) {
        throw supplierError(error, "تعذّر تحميل موردي ميزان 2.");
    }
}

export async function createMezanSupplier(payload) {
    try {
        return (await api.post("/suppliers-v2", payload)).data;
    } catch (error) {
        throw supplierError(error, "تعذّر إضافة المورد.");
    }
}

export async function updateMezanSupplier(supplierId, payload) {
    try {
        return (await api.put(
            `/suppliers-v2/${encodeURIComponent(supplierId)}`,
            payload,
        )).data;
    } catch (error) {
        throw supplierError(error, "تعذّر حفظ تعديلات المورد.");
    }
}
