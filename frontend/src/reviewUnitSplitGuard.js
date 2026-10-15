const quantity = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 1) return 1;
    return Math.max(1, Math.floor(number));
};

export function reviewMultiUnitItems(detail) {
    return (detail?.items || [])
        .map((item) => ({
            order_item_id: String(item?.order_item_id || "").trim(),
            name: String(item?.name || "منتج بدون اسم").trim() || "منتج بدون اسم",
            quantity: quantity(item?.quantity),
        }))
        .filter((item) => item.order_item_id && item.quantity > 1);
}

export function reviewUnitSplitWarning(detail) {
    const items = reviewMultiUnitItems(detail);
    if (!items.length) return null;

    const lines = items.map((item) => `• ${item.name}: ${item.quantity} قطع`).join("\n");
    return [
        "يوجد منتج بكمية أكبر من قطعة واحدة:",
        lines,
        "",
        "عند المتابعة سيقوم ميزان بفصل كل قطعة إلى بطاقة مستقلة داخل ملفات التجهيز فقط، ولكل قطعة QR مستقل.",
        "في التجميع والشحن سيبقى المنتج عنصرًا واحدًا بكميته الأصلية.",
        "",
        "موافق = متابعة والفصل تلقائيًا.",
        "إلغاء = العودة للفصل/المراجعة يدويًا قبل الاعتماد.",
    ].join("\n");
}

export function confirmReviewUnitSplit(detail, confirmFn = globalThis?.confirm) {
    const warning = reviewUnitSplitWarning(detail);
    if (!warning) return true;
    if (typeof confirmFn !== "function") return false;
    return Boolean(confirmFn(warning));
}
