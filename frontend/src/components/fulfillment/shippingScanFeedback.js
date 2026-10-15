export function shippingScanFeedback({ mode, result = {}, error = null, barcode = "" }) {
    const normalizedBarcode = String(barcode || "").trim();
    if (error?.code === "carrier_shipment_already_received") {
        const employeeName = error?.details?.employee_name;
        return {
            kind: "duplicate",
            title: "تم مسح الشحنة مسبقًا",
            message: employeeName
                ? `هذه الشحنة مضافة من قبل إلى عهدة ${employeeName}. لم تُضف مرة أخرى.`
                : "هذه الشحنة مضافة مسبقًا إلى عهدة موظف تسليم الشحن. لم تُضف مرة أخرى.",
            barcode: normalizedBarcode,
            actionLabel: "مسح شحنة أخرى",
        };
    }
    if (error) return null;
    if (mode === "confirm_print") {
        const storeCourier = result?.carrier_label_type === "store_courier";
        return result?.already_confirmed
            ? {
                kind: "duplicate",
                title: "تم مسح البوليصة مسبقًا",
                message: storeCourier
                    ? "سبق تأكيد طباعة بوليصة مندوب المتجر ولصقها على هذا الطلب. لم يُسجل تأكيد مكرر."
                    : "سبق التحقق من هذا الباركود وتأكيد أن الشحنة جاهزة. لم يُسجل تأكيد مكرر.",
                barcode: normalizedBarcode,
                actionLabel: "إغلاق",
            }
            : {
                kind: "success",
                title: storeCourier
                    ? "تم تأكيد الطباعة واللصق"
                    : "تم مسح الباركود بنجاح",
                message: storeCourier
                    ? "تم التحقق من QR وتأكيد لصق البوليصة على الطلب. انتقل الطلب إلى انتظار إسناده لمندوب التوصيل."
                    : "تم التحقق من البوليصة وتأكيد أن الشحنة جاهزة لتسليمها لموظف الشحن.",
                barcode: normalizedBarcode,
                actionLabel: "إغلاق",
            };
    }
    return {
        kind: "success",
        title: "تم استلام الشحنة بنجاح",
        message: "تم مسح الباركود وإضافة الشحنة إلى عهدتك لتسليمها إلى شركة الشحن.",
        barcode: normalizedBarcode,
        actionLabel: "مسح شحنة أخرى",
    };
}
