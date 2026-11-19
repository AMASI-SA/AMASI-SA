export const UNAVAILABLE = "غير متاح";
export const moneyPattern = /^(-?)(\d+)\.(\d{2})$/;
const grouped = value => value.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

// Presentation only: retain the exact server string, including cents. Neither
// display nor export calculates balances, fees, exchange rates or profit.
export function formatPositionMoney(value) {
    const match = typeof value === "string" ? moneyPattern.exec(value) : null;
    return match ? `${match[1]}${grouped(match[2])}.${match[3]}` : UNAVAILABLE;
}

export function formatPositionCount(value) {
    if (typeof value === "number") return Number.isSafeInteger(value) && value >= 0 ? grouped(String(value)) : UNAVAILABLE;
    const match = typeof value === "string" ? /^(\d+)(\.\d+)?$/.exec(value) : null;
    return match ? `${grouped(match[1])}${match[2] || ""}` : UNAVAILABLE;
}

export function positionText(value) {
    return typeof value === "string" && value.trim() ? value : UNAVAILABLE;
}

export function currencyCodes(report) {
    const available = Object.keys(report?.currencies || {});
    return [...new Set([...(Array.isArray(report?.currency_order) ? report.currency_order : []), ...available])]
        .filter(code => typeof code === "string" && available.includes(code));
}

export function isPositionContract(report) {
    return report?.schema_version === 1
        && report?.authority === "operational_balance_report"
        && typeof report?.status === "string"
        && report.currencies !== null && typeof report.currencies === "object" && !Array.isArray(report.currencies);
}

export const PAYMENT_LABELS = {
    cash: "نقدًا",
    bank_transfer: "تحويل بنكي",
    card_terminal: "شبكة / بطاقة",
    cod: "الدفع عند الاستلام",
};

export const COLLECTION_LABELS = {
    confirmed: "تحصيل مؤكد",
    courier_collection_unverified: "لم يُوثّق تحصيل الشركة من العميل",
    pending_review: "بانتظار اعتماد التحصيل",
    not_confirmed: "التحصيل غير مؤكد",
    not_applicable: "لا يوجد تحصيل عند التسليم",
    unavailable: "حالة التحصيل غير متاحة",
};

export const DESTINATION_LABELS = {
    store_bank_account: "بنك المتجر · تحصيل مثبت",
    store_pos_receivable: "ذمة شبكة المتجر · بانتظار التسوية",
    driver_custody: "عهدة نقدية لدى المندوب",
    courier_receivable: "ذمة الدفع عند الاستلام لدى شركة الشحن",
    store_bank_account_unknown: "تحويل للمتجر · الحساب البنكي غير متاح",
    store_pos_account_unknown: "شبكة المتجر · حساب التسوية غير متاح",
    not_applicable: "لا توجد جهة تحصيل عند التسليم",
    unavailable: "جهة التحصيل غير متاحة",
};

export const PARTY_LABELS = { courier: "شركة شحن", store_driver: "مندوب المتجر" };
export const DELIVERY_SCOPE_NOTE = "المتبقي وفق مصدر الطلب معلومة من الطلب، ولا يُضاف مرة أخرى إلى تحصيل شركة الشحن أو عهدة المندوب.";
export const paymentLabel = value => PAYMENT_LABELS[value] || UNAVAILABLE;
export const collectionLabel = value => COLLECTION_LABELS[value] || COLLECTION_LABELS.unavailable;
export const destinationLabel = value => DESTINATION_LABELS[value] || DESTINATION_LABELS.unavailable;

export const DELIVERY_MONEY_COLUMNS = [
    { key: "shipping_confirmed", label: "أجرة الشحن المؤكدة", role: "payable" },
    { key: "shipping_settled", label: "المسدد من الأجرة", role: "neutral" },
    { key: "shipping_outstanding", label: "المتبقي من الأجرة", role: "payable" },
    { key: "collection_amount", label: "مبلغ التحصيل", role: "neutral" },
    { key: "driver_custody", label: "عهدة المندوب", role: "neutral" },
    { key: "collection_settled", label: "السداد المخصص للتحصيل", role: "neutral" },
    { key: "collection_outstanding", label: "المتبقي لدى المنفذ", role: "receivable" },
    { key: "store_collection", label: "تحصيل المتجر", role: "neutral" },
    { key: "customer_outstanding", label: "المتبقي وفق مصدر الطلب", role: "neutral" },
    { key: "courier_responsibility", label: "ذمة الشركة الأصلية", role: "receivable" },
];

export function deliveryPartyKey(row) {
    return JSON.stringify([row?.party_type || null, row?.party_id || null, row?.party_name || null]);
}

export function deliveryOrderLabel(row) {
    if (positionText(row?.order_number) !== UNAVAILABLE) return row.order_number;
    return positionText(row?.order_id) !== UNAVAILABLE ? `معرف الطلب: ${row.order_id}` : UNAVAILABLE;
}

export const isAramexStatusFee = row => row?.fee_recognition_basis === "aramex_status_created_after_start";

export function shippingFeeStatusLabel(row) {
    const label = { completed: "تم التنفيذ", out_for_delivery: "جاري التوصيل", delivered: "تم التوصيل" }[row?.fee_observed_order_status];
    return typeof label === "string" ? label : UNAVAILABLE;
}

export function deliveryRecognitionLabel(row) {
    if (row?.recognition_basis === "owner_confirmed_after_start") return "تأكيد المالك بالتوصيل بعد بدء التشغيل";
    if (row?.recognition_basis === "delivered_order_created_after_start") return "طلب تم توصيله وأُنشئ بعد بدء التشغيل";
    return row?.delivered_at ? "عند تم التوصيل" : UNAVAILABLE;
}

export function shippingRecognitionLabel(row) {
    if (isAramexStatusFee(row)) return "مدفوع مقدمًا وفق حالة طلب أرامكس بعد بدء التشغيل";
    if (row?.recognition_event === "order_completed") return row.payment_mode === "deferred" ? "أجرة مستحقة عند تم التنفيذ" : "مدفوع مقدمًا عند تم التنفيذ";
    return deliveryRecognitionLabel(row);
}

export function deliveryParties(rows = []) {
    return [...new Map(rows.map(row => [deliveryPartyKey(row), {
        key: deliveryPartyKey(row),
        label: `${positionText(row.party_name)}${PARTY_LABELS[row.party_type] ? ` · ${PARTY_LABELS[row.party_type]}` : ""}`,
    }])).values()];
}

export function filterDeliveries(rows = [], filters = {}) {
    const order = String(filters.order || "").trim().toLocaleLowerCase();
    return rows.filter(row => (!order || [row.order_number, row.order_id].some(value => typeof value === "string" && value.toLocaleLowerCase().includes(order)))
        && (!filters.party || deliveryPartyKey(row) === filters.party)
        && (!filters.payment || (row.payment_method || "unavailable") === filters.payment))
        // Older reports used "confirmed" for the courier's booked responsibility.
        // This field did not attest customer collection.
        .map(row => row.party_type === "courier" && row.collection_status === "confirmed"
            ? { ...row, collection_status: "courier_collection_unverified", collection_amount: null } : row);
}

export function readableMessages(items = []) {
    return [...new Set(items.map(item => typeof item === "string" ? item : item?.message)
        .filter(value => typeof value === "string" && value.trim()))];
}
