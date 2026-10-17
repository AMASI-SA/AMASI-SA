import api from "../lib/api";

export async function getProductBundleComponents(id) {
    return (await api.get(`/products-v2/${encodeURIComponent(id)}/bundle-components`)).data;
}

export async function getProductBundle(id) {
    return (await api.get(`/products-v2/${encodeURIComponent(id)}/bundle`)).data;
}
export async function saveProductBundle(id, payload) {
    return (await api.put(`/products-v2/${encodeURIComponent(id)}/bundle`, payload)).data;
}
export async function getOrderBundles(orderNumber) {
    return (await api.get(`/fulfillment-v2/orders/${encodeURIComponent(orderNumber)}/bundles`)).data;
}
export async function previewProductBundle(id, selectedOptions, variantId, context = {}) {
    return (await api.post(`/products-v2/${encodeURIComponent(id)}/bundle/preview`, { selected_options: selectedOptions, ...(variantId ? { variant_id: variantId } : {}), ...context })).data;
}
export async function refreshOrderBundles(orderNumber) {
    return (await api.post(`/fulfillment-v2/orders/${encodeURIComponent(orderNumber)}/bundles/refresh`)).data;
}
export async function linkBundleReceipt(orderNumber, payload) {
    return (await api.post(`/fulfillment-v2/orders/${encodeURIComponent(orderNumber)}/bundles/receipts`, payload)).data;
}
export async function changeBundleSource(orderNumber, payload) {
    return (await api.post(`/fulfillment-v2/orders/${encodeURIComponent(orderNumber)}/bundles/source`, payload)).data;
}
export async function getBundleReport() {
    return (await api.get("/fulfillment-v2/bundles/report")).data;
}

export function bundleOptionVisible(option, choices) {
    const conditions = option.show_if || option.conditions || [];
    if (!Array.isArray(conditions)) return false;
    return conditions.every((condition) => choices.some((choice) => choice.option_id === String(condition.option_id) && choice.value_id === String(condition.value_id)));
}

export function bundleError(error) {
    const detail = error?.response?.data?.detail;
    const messages = {
        bundle_policy_revision_conflict: "تغيرت سياسة المنتج في جلسة أخرى. اضغط «إعادة تحميل السياسة» ثم راجع التغييرات وأعد الحفظ.",
        bundle_policy_evidence_required: "أدخل مرجع فاتورة أو اتفاق مورد يثبت أساس التكلفة والتجهيز قبل حفظ السياسة.",
        bundle_policy_unknown_component: "تغيرت مكونات المنتج الأصلية. أعد تحميل السياسة للتحقق من الروابط المتاحة.",
        bundle_policy_unknown_variant: "نسخة المنتج المرتبطة بالسياسة لم تعد متاحة. اختر نسخة موجودة أو احذف القاعدة.",
        bundle_policy_unknown_option: "أحد خيارات السياسة أو قيمه لم يعد متاحًا. أعد تحميل السياسة وصحح الربط.",
        bundle_policy_invalid_rule: "أكمل شروط قاعدة التوريد أو نسخة المنتج أو نص التخصيص، أو احذف القاعدة الفارغة.",
        bundle_policy_duplicate_rule: "توجد قاعدة توريد مكررة. احذف القاعدة المكررة ثم أعد الحفظ.",
        bundle_policy_invalid_customization: "ربط التخصيص يحتاج حقلًا نصيًا مدعومًا وقيمة نصية صحيحة.",
        bundle_component_policy_unresolved: "تكلفة بعض المكونات غير محسومة. افتح سياسة المنتج الأصلية وحدد أساس التكلفة والتجهيز مع مرجع موثوق.",
        bundle_invalid_option_mapping: "صحح ربط حقل البكج وقيمته بخيار المنتج الداخلي وقيمته.",
        bundle_invalid_customization_mapping: "صحح ربط النص بين البكج والمنتج الداخلي؛ لا يمكن ربط الملفات أو التواريخ كنص تخصيص.",
        bundle_invalid_mapping: "أحد روابط اختيارات المنتج الداخلي غير صالح. راجع الحقول والقيم والنسخة المطلوبة.",
        supplier_v2_identity_required: "المورد المحدد غير موجود في سجل الموردين الحالي. أعد تحميل الموردين واختر موردًا صالحًا.",
        supplier_verified_identity_required: "هوية المورد غير موثقة. أكمل توثيق المورد قبل استخدامه في التوريد.",
        bundle_cycle: "لا يمكن حفظ التركيب: يوجد ربط دائري بين البكجات.",
        bundle_revision_conflict: "تغير تركيب البكج في جلسة أخرى. أعد تحميل التركيب قبل الحفظ.",
        bundle_depth_limit: "تجاوز التركيب الحد الآمن: خمسة مستويات تداخل.",
        bundle_leaf_limit: "تجاوز التركيب الحد الآمن: 500 مكوّن نهائي.",
        bundle_graph_limit: "حجم شجرة المكونات يتجاوز الحد الآمن.",
        bundle_item_limit: "يجب أن يحتوي البكج من مكوّن واحد إلى 100 مكوّن مباشر.",
        bundle_supplier_required: "اختر موردًا لكل مكوّن توريده من المورد.",
        bundle_location_not_found: "موقع المخزون غير موجود. تحقق من معرّف الموقع.",
        bundle_variant_required: "اختر متغير المنتج قبل حساب المكونات.",
        bundle_variant_not_found: "المتغير المحدد غير موجود في هذا المنتج.",
        bundle_stock_customization_requires_variant: "المقاس النصي يحتاج موردًا أو متغير مخزون محددًا؛ لن ينشئ ميزان متغيرًا افتراضيًا.",
        bundle_component_scope_required: "حدد نطاق مكونات المنتج الأصلية: لكل بكج أو لكل وحدة فعلية.",
        bundle_nonstock_supplier_requires_service_receipt: "الخدمة غير المخزنية تحتاج مسار اعتماد استلام خدمة غير متاح حاليًا. استخدم تكلفة الخدمة الداخلية أو مكوّنًا مخزنيًا قابلًا للاستلام.",
        bundle_cost_only_source_change_forbidden: "هذا البند تكلفة تشغيل مباشرة ولا يمثل مكوّنًا قابلًا لتغيير مصدر التوريد.",
        bundle_purchase_receipt_customization_mismatch: "تخصيص إيصال الشراء لا يطابق القطعة المطلوبة. تحقق من المقاس والخصائص ومعرّفات حقولها.",
        bundle_resource_cost_overlap: "المكوّن مرتبط أيضًا بتكلفة المنتج؛ أزل الربط المتكرر لمنع احتساب التكلفة مرتين.",
        bundle_parent_direct_cost_requires_component: "حوّل تكلفة البكج المباشرة إلى مكوّن مستقل لتجنب احتساب التكلفة مرتين.",
        bundle_duplicate_item: "المكوّن مكرر في التركيب. اجمع كمياته في صف واحد.",
        bundle_duplicate_quantity_conflict: "تكررت كميات مختلفة للمكوّن؛ اختر جمع الكميات صراحةً أو وحّد الكمية.",
        bundle_hidden_option_selected: "أحد الخيارات المختارة مخفي بشرط آخر. راجع الاختيارات.",
        bundle_purchase_receipt_identity_mismatch: "إيصال الشراء لا يطابق المكوّن المطلوب أو لم يُرحّل.",
        bundle_purchase_supplier_mismatch: "مورد إيصال الشراء لا يطابق مورد المكوّن.",
    };
    return messages[detail?.code] || detail?.message || (typeof detail === "string" ? detail : "تعذّر تنفيذ العملية. حدّث البيانات وتحقق من المكونات والموردين وحدود التداخل.");
}

export function bundleMoney(value) {
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "غير متاح";
    return `${Number(value).toLocaleString("ar-SA", { maximumFractionDigits: 2 })} ر.س`;
}

export function bundleLeafCost(leaf) {
    if (leaf.estimated_cost != null) return leaf.estimated_cost;
    if (leaf.unit_cost == null || leaf.quantity == null) return null;
    const result = Number(leaf.unit_cost) * Number(leaf.quantity);
    return Number.isFinite(result) ? result : null;
}
