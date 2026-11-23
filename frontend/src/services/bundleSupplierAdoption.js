import api from "../lib/api";
import { bundleError } from "./productBundles";

const path = (orderNumber) => `/fulfillment-v2/orders/${encodeURIComponent(orderNumber)}/bundle-adoption`;

export async function previewBundleAdoption(orderNumber, orderLineId) {
    return (await api.post(`${path(orderNumber)}/preview`, { order_line_id: orderLineId })).data;
}

export async function applyBundleAdoption(orderNumber, payload) {
    return (await api.post(`${path(orderNumber)}/apply`, payload)).data;
}

export function newBundleAdoptionRequestId() {
    if (globalThis.crypto?.randomUUID) return `bundle-adoption:${globalThis.crypto.randomUUID()}`;
    if (!globalThis.crypto?.getRandomValues) throw new Error("تعذّر إنشاء مرجع العملية. حدّث المتصفح وأعد المحاولة.");
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return `bundle-adoption:${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

export function bundleAdoptionError(error) {
    const code = error?.response?.data?.detail?.code;
    const labels = {
        bundle_adoption_owner_required: "تحويل التجهيز متاح لمالك الحساب فقط.",
        bundle_adoption_preview_changed: "تغيّرت بيانات الطلب أو التكاليف بعد المعاينة. راجع معاينة جديدة قبل التحويل.",
        bundle_adoption_source_changed: "تغيّرت بيانات الطلب. حدّث الطلب ثم أعد معاينة القطع.",
        bundle_adoption_source_not_eligible: "يجب أن يكون الطلب قيد التنفيذ، ومراجعًا، وبياناته محدثة قبل تحويل التجهيز.",
        bundle_adoption_unreceived_dispatched_pieces_required: "يتطلب التحويل قطعًا أُرسلت للمورد ولم يبدأ استلامها. راجع حالة التجهيز والاستلام.",
        bundle_adoption_receipt_or_scan_exists: "بدأ استلام إحدى القطع سابقًا. راجع جلسة الاستلام قبل تصحيح التجهيز.",
        bundle_adoption_invoice_exists: "توجد فاتورة مرتبطة بإحدى القطع. راجع الفاتورة قبل تصحيح التجهيز.",
        bundle_adoption_unconsumed_empty_units_required: "توجد حركة مخزون أو تجهيز سابقة تحتاج مراجعة قبل التحويل.",
        bundle_adoption_dispatch_custody_required: "تعذّر تأكيد المورد وموظف التجهيز من سجل الإرسال. راجع إسناد القطع أولًا.",
        bundle_adoption_catalog_evidence_required: "أكمل تعريف البكج وسياسة التوريد والتكاليف في بطاقة المنتج أولًا.",
        bundle_adoption_bundle_incomplete: "بعض مكونات البكج أو تكاليفها غير مكتملة. راجع بطاقة المنتج واختيارات الطلب.",
        bundle_adoption_finished_same_product_required: "هذا التحويل مخصص لطقم من قطع جاهزة من المنتج نفسه. راجع الخدمات والمكونات الإضافية في إعدادات البكج.",
        bundle_supplier_unsupported_recipe: "يتطلب هذا المسار قطعة أو قطعتين جاهزتين من المنتج نفسه، مع تكلفة ومورد محددين لكل قطعة.",
        bundle_adoption_ordinary_intake_required: "سبق تجهيز هذا المنتج كبكج. راجع القطع الحالية في تتبع الطلب.",
        bundle_adoption_ordinary_plan_required: "خطة تجهيز المنتج لم تعد قابلة لهذا التحويل. حدّث الطلب وراجع قطعه الحالية.",
        bundle_adoption_request_conflict: "مرجع التحويل مرتبط بعملية مختلفة. حدّث الطلب ثم أعد المعاينة.",
    };
    if (labels[code]) return labels[code];
    if (!error?.response && error?.message) return error.message;
    return bundleError(error);
}
