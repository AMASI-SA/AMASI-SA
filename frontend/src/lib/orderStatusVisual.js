const STATUS_VISUALS = Object.freeze({
    under_review: { dot: "bg-slate-800", iconBox: "bg-slate-100 text-slate-700", active: "border-slate-700 bg-slate-50 ring-2 ring-slate-200" },
    reviewed: { dot: "bg-slate-800", iconBox: "bg-slate-100 text-slate-700", active: "border-slate-700 bg-slate-50 ring-2 ring-slate-200" },
    processing: { dot: "bg-sky-500", iconBox: "bg-sky-50 text-sky-600", active: "border-sky-500 bg-sky-50 ring-2 ring-sky-100" },
    completed: { dot: "bg-emerald-400", iconBox: "bg-emerald-50 text-emerald-600", active: "border-emerald-500 bg-emerald-50 ring-2 ring-emerald-100" },
    delivering: { dot: "bg-amber-400", iconBox: "bg-amber-50 text-amber-600", active: "border-amber-500 bg-amber-50 ring-2 ring-amber-100" },
    delivered: { dot: "bg-emerald-400", iconBox: "bg-emerald-50 text-emerald-600", active: "border-emerald-500 bg-emerald-50 ring-2 ring-emerald-100" },
    payment: { dot: "bg-rose-500", iconBox: "bg-rose-50 text-rose-600", active: "border-rose-500 bg-rose-50 ring-2 ring-rose-100" },
    fulfillment: { dot: "bg-teal-400", iconBox: "bg-teal-50 text-teal-600", active: "border-teal-500 bg-teal-50 ring-2 ring-teal-100" },
    review: { dot: "bg-slate-800", iconBox: "bg-slate-100 text-slate-700", active: "border-slate-700 bg-slate-50 ring-2 ring-slate-200" },
    cancelled: { dot: "bg-rose-500", iconBox: "bg-rose-50 text-rose-600", active: "border-rose-500 bg-rose-50 ring-2 ring-rose-100" },
    refunded: { dot: "bg-rose-500", iconBox: "bg-rose-50 text-rose-600", active: "border-rose-500 bg-rose-50 ring-2 ring-rose-100" },
    default: { dot: "bg-violet-500", iconBox: "bg-violet-50 text-violet-600", active: "border-violet-500 bg-violet-50 ring-2 ring-violet-100" },
});

export function normalizeOrderStatus(status) {
    return String(status || "").replaceAll("_", " ").trim().toLowerCase();
}

export function orderStatusKind(status) {
    const value = normalizeOrderStatus(status);
    if (value.includes("بإنتظار المراجعة") || value.includes("بانتظار المراجعة") || value === "under review") return "under_review";
    if (value.includes("تم المراجعة") || value.includes("تمت المراجعة") || value === "reviewed") return "reviewed";
    if (value.includes("قيد التنفيذ") || value.includes("جاري التنفيذ") || value.includes("processing") || value.includes("مدمج")) return "processing";
    if (value === "تم التنفيذ" || value === "completed") return "completed";
    if (value.includes("جاري التوصيل") || value.includes("delivering") || value.includes("out for delivery")) return "delivering";
    if (value === "تم التوصيل" || value === "delivered") return "delivered";
    if (value.includes("الدفع") || value.includes("payment")) return "payment";
    if (value.includes("التجهيز") || value.includes("الشحن") || value.includes("مندوب")) return "fulfillment";
    if (value.includes("مراجعة") || value.includes("تأكيد العميل") || value.includes("الملاحظات")) return "review";
    if (value.includes("ملغ") || value.includes("محذوف") || value.includes("cancel") || value.includes("deleted")) return "cancelled";
    if (value.includes("مسترج") || value.includes("استرجاع") || value.includes("refund") || value.includes("return")) return "refunded";
    return "default";
}

export function orderStatusVisualClasses(status) {
    return STATUS_VISUALS[orderStatusKind(status)] || STATUS_VISUALS.default;
}

export function orderStatusDotClass(status) {
    return orderStatusVisualClasses(status).dot;
}

export function isWaitingForPaymentOrder(order) {
    return [order?.status_native, order?.status, order?.payment?.status]
        .some((status) => {
            const value = normalizeOrderStatus(status);
            if (value.includes("الدفع") && (value.includes("انتظار") || value.includes("بإنتظار"))) return true;
            return value.includes("payment")
                && ["pending", "awaiting", "waiting"].some((marker) => value.includes(marker));
        });
}
