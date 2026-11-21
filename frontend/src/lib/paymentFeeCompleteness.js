/** An explicit incomplete fee total must never become a zero-fee profit. */
export function paymentFeeCompleteness(totals = {}) {
    const complete = totals.payment_fees_complete !== false;
    const reasons = Array.isArray(totals.payment_fee_review_reasons)
        ? totals.payment_fee_review_reasons : [];
    const awaitingCollection = reasons.includes("payment_awaiting_collection");
    const otherReasons = reasons.some((reason) => reason !== "payment_awaiting_collection");
    const hint = complete ? null
        : awaitingCollection && otherReasons ? "بانتظار تحصيل بعض الطلبات واستكمال بيانات عمولات الدفع"
            : awaitingCollection ? "بانتظار تحصيل بعض الطلبات"
                : "بانتظار اكتمال بيانات عمولات الدفع";
    return { complete, hint };
}
