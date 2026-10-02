import api from "../lib/api";

const ERROR_LABELS = {
    supplier_company_name_exists: "يوجد مورد في ميزان 2 بالاسم نفسه.",
    supplier_service_required: "اختر خدمة واحدة على الأقل يقدمها المورد.",
    supplier_service_not_found: "إحدى الخدمات المحددة لم تعد موجودة في كتالوج الخدمات.",
    mezan_supplier_not_found: "المورد غير موجود في ميزان 2.",
    supplier_amount_invalid: "أدخل مبلغًا موجبًا بمنزلتين عشريتين كحد أقصى.",
    supplier_requires_v2_active: "يلزم تفعيل المحاسبة في ميزان 2 قبل تسجيل السداد.",
    supplier_opening_or_ledger_not_ready: "الأرصدة الافتتاحية أو السجل المالي غير جاهزة.",
    supplier_opening_zero_evidence_invalid: "توثيق الرصيد الافتتاحي الصفري يحتاج مراجعة.",
    supplier_accounts_require_documented_opening: "يلزم توثيق الرصيد الافتتاحي لحساب المورد.",
    supplier_scope_too_large: "حجم سجل الموردين يتجاوز الحد المتاح؛ تواصل مع المسؤول.",
    supplier_invoice_allocation_requires_reconciliation: "تخصيصات الفاتورة تحتاج مراجعة وتسوية قبل المتابعة.",
    supplier_balance_requires_reconciliation: "رصيد المورد يحتاج مراجعة وتسوية قبل المتابعة.",
    supplier_allocation_reversal_requires_reconciliation: "عكس التخصيص يحتاج مراجعة وتسوية قبل المتابعة.",
    supplier_payment_reversal_requires_reconciliation: "عكس السداد يحتاج مراجعة وتسوية قبل المتابعة.",
    supplier_payment_allocation_requires_reconciliation: "تخصيصات السداد تحتاج مراجعة وتسوية قبل المتابعة.",
    supplier_v2_not_found: "المورد غير موجود في ميزان 2.",
    supplier_overpayment_requires_explicit_advance: "المبلغ يتجاوز المستحق؛ وافق صراحةً على تسجيل الزيادة كدفعة مقدمة.",
    supplier_payment_evidence_unavailable: "ملف الدليل غير متاح أو غير صالح لهذه العملية.",
    supplier_payment_future_date: "لا يمكن تسجيل السداد بتاريخ مستقبلي.",
    supplier_payment_before_cutover: "يجب أن يكون تاريخ السداد بعد الانتقال إلى ميزان 2.",
    supplier_payment_backdating_requires_reconciliation: "تاريخ السداد السابق يحتاج مراجعة وتسوية.",
    supplier_actor_scope_changed: "تغيّر نطاق صلاحياتك؛ حدّث الصفحة قبل المتابعة.",
    supplier_operation_payload_conflict: "رقم العملية مستخدم ببيانات مختلفة؛ راجع نتيجة العملية السابقة.",
    supplier_native_financial_invoice_required: "اختر فاتورة مثبتة في السجل المالي المعتمد لميزان 2.",
    supplier_allocation_exceeds_available: "مبلغ التخصيص يتجاوز الرصيد المتاح أو المتبقي على الفاتورة.",
    supplier_allocation_split_required: "قسّم التخصيص إلى عمليتين منفصلتين للسداد غير المخصص والدفعة المقدمة.",
    supplier_allocation_backdating_requires_reconciliation: "تاريخ التخصيص السابق يحتاج مراجعة وتسوية.",
    track_a_canonical_account_contract_required: "الحسابات المالية غير جاهزة لتسجيل السداد.",
    canonical_payment_account_contract_invalid: "حساب السداد غير مؤهل؛ اختر حسابًا ماليًا معتمدًا.",
    supplier_insufficient_canonical_funds: "الرصيد المتاح في حساب البنك أو الصندوق غير كافٍ.",
    fulfillment_permission_required: "لا تملك صلاحية إدارة موردي ميزان 2.",
};

function supplierError(error, fallback) {
    const detail = error?.response?.data?.detail;
    const validation = Array.isArray(detail)
        ? detail.find((row) => String(row?.msg || "").includes("supplier_service_required"))
        : null;
    const code = detail?.code || (validation ? "supplier_service_required" : "");
    const result = new Error(
        ERROR_LABELS[code]
        || (Array.isArray(detail) ? "راجع الحقول المطلوبة وصيغة المبلغ والتاريخ والمرجع." : null)
        || (error?.response ? fallback : error?.message)
        || fallback,
    );
    result.status = error?.response?.status;
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

export async function loadMezanSupplierFinancials({ supplierId = null } = {}) {
    try {
        return (await api.get("/suppliers-v2/payment-workspace", {
            params: supplierId ? { supplier_id: supplierId } : {},
        })).data;
    } catch (error) {
        throw supplierError(error, "تعذّر تحميل فواتير الموردين ومديونياتهم.");
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

export async function recordMezanSupplierPayment(supplierId, payload) {
    try {
        return (await api.post(`/suppliers-v2/${encodeURIComponent(supplierId)}/payments`, payload)).data;
    } catch (error) {
        throw supplierError(error, "تعذّر تسجيل السداد. أعد المحاولة بنفس العملية.");
    }
}

export async function allocateMezanSupplierPayment(supplierId, payload) {
    try {
        return (await api.post(`/suppliers-v2/${encodeURIComponent(supplierId)}/allocations`, payload)).data;
    } catch (error) {
        throw supplierError(error, "تعذّر تخصيص الدفعة. أعد المحاولة بنفس العملية.");
    }
}
