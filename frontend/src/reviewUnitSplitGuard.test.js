import { reviewMultiUnitItems, reviewUnitSplitWarning } from "./reviewUnitSplitGuard";

test("detects only reviewed items with quantity greater than one", () => {
    expect(reviewMultiUnitItems({ items: [
        { order_item_id: "a", name: "منتج 1", quantity: 1 },
        { order_item_id: "b", name: "منتج 2", quantity: 2 },
    ] })).toEqual([{ order_item_id: "b", name: "منتج 2", quantity: 2 }]);
});

test("warning explains preparation-only split and aggregated shipping", () => {
    const message = reviewUnitSplitWarning({ items: [
        { order_item_id: "b", name: "دقلة", quantity: 2 },
    ] });
    expect(message).toContain("دقلة: 2 قطع");
    expect(message).toContain("بطاقة مستقلة داخل ملفات التجهيز فقط");
    expect(message).toContain("التجميع والشحن");
    expect(message).toContain("بكميته الأصلية");
});

test("single-unit review does not require a warning", () => {
    expect(reviewUnitSplitWarning({ items: [{ order_item_id: "a", quantity: 1 }] })).toBeNull();
});
