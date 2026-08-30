import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import LatestSoldProductsCard, {
    expandSoldProductRows,
    mergeOrdersNewestFirst,
    sortOrdersNewestFirst,
} from "./LatestSoldProductsCard";

jest.mock("react-router-dom", () => {
    const ReactModule = require("react");
    return {
        Link: ({ children, to, ...props }) => ReactModule.createElement("a", { ...props, href: to }, children),
    };
});

const order = (orderNumber, productName, quantity = 1) => ({
    order_number: orderNumber,
    created_at: "2026-08-28T10:30:00Z",
    items: [{
        order_item_id: `item-${orderNumber}`,
        name: productName,
        quantity,
        image_url: `https://cdn.example.com/${orderNumber}.png`,
    }],
});

test("sorts by order number descending and expands every sold unit into its own row", () => {
    const sorted = sortOrdersNewestFirst([
        order("280000001", "الأقدم"),
        order("280000003", "الأحدث", 3),
        order("280000002", "الأوسط"),
    ]);
    const rows = expandSoldProductRows(sorted);

    expect(sorted.map((item) => item.order_number)).toEqual(["280000003", "280000002", "280000001"]);
    expect(rows.filter((row) => row.item.name === "الأحدث")).toHaveLength(3);
});

test("shows products from the newest five orders initially without aggregation", () => {
    const markup = renderToStaticMarkup(<LatestSoldProductsCard orders={[
        order("280000001", "منتج الطلب الأول"),
        order("280000006", "منتج الطلب السادس", 2),
        order("280000004", "منتج الطلب الرابع"),
        order("280000002", "منتج الطلب الثاني"),
        order("280000005", "منتج الطلب الخامس"),
        order("280000003", "منتج الطلب الثالث"),
    ]} />);

    expect(markup).toContain("أحدث المنتجات المباعة");
    expect(markup).toContain("5 طلبات");
    expect(markup.match(/منتج الطلب السادس/g)).toHaveLength(2);
    expect(markup.indexOf("منتج الطلب السادس")).toBeLessThan(markup.indexOf("منتج الطلب الخامس"));
    expect(markup).toContain("قطعة 1 من 2");
    expect(markup).toContain("قطعة 2 من 2");
    expect(markup).not.toContain("منتج الطلب الأول");
    expect(markup).toContain("طلب #280000006");
    expect(markup).toContain("المزيد");
    expect(markup).toContain("data-testid=\"latest-sold-products-scroll\"");
    expect(markup).toContain("h-[520px]");
    expect(markup).toContain("overflow-y-auto");
});

test("merges an automatically refreshed newest order at the top", () => {
    const merged = mergeOrdersNewestFirst(
        [
            order("280000006", "نسخة قديمة"),
            order("280000005", "الطلب السابق"),
        ],
        [
            order("280000007", "الطلب الجديد"),
            order("280000006", "نسخة محدثة"),
        ],
    );

    expect(merged.map((item) => item.order_number)).toEqual([
        "280000007",
        "280000006",
        "280000005",
    ]);
    expect(merged[1].items[0].name).toBe("نسخة محدثة");
});

test("excludes a payment-pending order before selecting the newest five sold orders", () => {
    const pendingOrder = {
        ...order("280000007", "منتج غير مدفوع"),
        status: "payment_pending",
        status_native: "بانتظار الدفع",
    };
    const markup = renderToStaticMarkup(<LatestSoldProductsCard orders={[
        pendingOrder,
        order("280000006", "منتج مدفوع 6"),
        order("280000005", "منتج مدفوع 5"),
        order("280000004", "منتج مدفوع 4"),
        order("280000003", "منتج مدفوع 3"),
        order("280000002", "منتج مدفوع 2"),
        order("280000001", "منتج مدفوع 1"),
    ]} />);

    expect(markup).toContain("5 طلبات");
    expect(markup).not.toContain("منتج غير مدفوع");
    expect(markup).toContain("منتج مدفوع 2");
    expect(markup).not.toContain("منتج مدفوع 1");
});
