import {
    isWaitingForPaymentOrder,
    orderStatusDotClass,
} from "./orderStatusVisual";

test.each([
    ["بانتظار الدفع", "bg-rose-500"],
    ["قيد التنفيذ", "bg-sky-500"],
    ["تم التنفيذ", "bg-emerald-400"],
    ["جاري التوصيل", "bg-amber-400"],
])("maps %s to the Orders V2 dot color", (status, expectedClass) => {
    expect(orderStatusDotClass(status)).toBe(expectedClass);
});

test("only treats a genuinely payment-pending order as unsold", () => {
    expect(isWaitingForPaymentOrder({ status: "payment_pending" })).toBe(true);
    expect(isWaitingForPaymentOrder({ status_native: "بانتظار الدفع" })).toBe(true);
    expect(isWaitingForPaymentOrder({ status: "completed", payment: { status: "paid" } })).toBe(false);
    expect(isWaitingForPaymentOrder({ status: "completed", payment: { status: "payment_success" } })).toBe(false);
});
