import { paymentFeeEstimateLabel } from "./paymentFeeRows";

/** An explicit incomplete fee total must never become a zero-fee profit. */
export function paymentFeeCompleteness(totals = {}) {
    const complete = totals.payment_fees_complete !== false;
    const reasons = Array.isArray(totals.payment_fee_review_reasons)
        ? totals.payment_fee_review_reasons : [];
    const awaitingCollection = reasons.includes("payment_awaiting_collection");
    const splitPaymentReview = reasons.includes("provider_split_payment_review_required");
    const otherReasons = reasons.some((reason) => reason !== "payment_awaiting_collection"
        && reason !== "provider_split_payment_review_required");
    const pendingActions = [];
    if (awaitingCollection) pendingActions.push("تحصيل بعض الطلبات");
    if (splitPaymentReview) pendingActions.push(awaitingCollection
        ? "مطابقة الدفعات المتعددة" : "مطابقة الدفعات المتعددة لبعض الطلبات");
    if (otherReasons || pendingActions.length === 0) pendingActions.push(pendingActions.length
        ? "استكمال بيانات عمولات الدفع" : "اكتمال بيانات عمولات الدفع");
    const hint = complete ? null : `بانتظار ${pendingActions.join(" و")}`;
    const estimated = totals.payment_fees_estimated === true;
    const estimateSources = estimated && Array.isArray(totals.payment_fee_estimate_sources)
        ? totals.payment_fee_estimate_sources : [];
    return { complete, hint, estimated, estimateSources,
        estimateLabel: estimated ? paymentFeeEstimateLabel(estimateSources) : null };
}

/** Cohort review blocks final profit without inventing a missing commission. */
export function financialCohortCompleteness(totals = {}, cohort = null) {
    const complete = totals.financial_cohort_complete !== false && cohort?.complete !== false;
    const excluded = cohort?.excluded_unpaid_orders ?? totals.excluded_unpaid_orders;
    const excludedOrders = typeof excluded === "number" && Number.isInteger(excluded) && excluded > 0 ? excluded : 0;
    const deferred = (cohort?.review_reason_counts?.awaiting_payment_history_deferred ?? 0) > 0;
    return { complete, excludedOrders, hint: complete ? null : deferred
        ? "فحص تاريخ الطلبات معلّق؛ راجع الأرصدة التشغيلية. صافي الربح غير مؤكد"
        : "بانتظار مراجعة الإثبات المالي للطلبات" };
}
