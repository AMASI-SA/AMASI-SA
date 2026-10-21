import { storeCourierLabelHtml } from "./storeCourierLabelPrint";

test("store courier label is A6 and uses order/customer facts", () => {
    const html = storeCourierLabelHtml({
        order_number: "276628330",
        barcode_value: "276628330",
        qr_code: "data:image/svg+xml;base64,QR",
        store_name: "متجر ميزان",
        customer_name: "العميل",
        customer_phone: "0500000000",
        address: { city: "الرياض", address_line: "حي العود" },
        remaining_amount: { amount: 0, currency: "SAR" },
        items: [{ name: "سلسال", quantity: 1 }],
    });

    expect(html).toContain("size: A6 portrait");
    expect(html).toContain("276628330");
    expect(html).toContain("العميل");
    expect(html).toContain("حي العود");
    expect(html).not.toContain("سلسال");
});

test("courier address keeps district and street alongside house and national address", () => {
    const html = storeCourierLabelHtml({
        address: { address_line_two: "منزل 65", district: "حي الاختبار",
            street: "شارع الاختبار", city: { name: "الرياض" }, short_address: "TEST1234" },
        items: [{ name: "PRODUCT_MUST_NOT_PRINT", quantity: 2 }],
        remaining_amount: { amount: 0, currency: "SAR" },
    });
    for (const value of ["منزل 65", "حي الاختبار", "شارع الاختبار", "الرياض", "TEST1234", "0.00 SAR"]) {
        expect(html).toContain(value);
    }
    expect(html).not.toContain("PRODUCT_MUST_NOT_PRINT");
    expect(html).not.toContain("[object Object]");
});

test("courier address aliases are escaped and duplicate values appear once", () => {
    const html = storeCourierLabelHtml({ address: {
        address_line: "حي آمن", neighborhood: "حي آمن", street_name: "شارع <اختبار>",
    } });
    expect(html.split("حي آمن")).toHaveLength(2);
    expect(html).toContain("شارع &lt;اختبار&gt;");
    expect(html).not.toContain("شارع <اختبار>");
});

test("store courier label shows the COD balance with two decimals", () => {
    const html = storeCourierLabelHtml({
        order_number: "test-order",
        remaining_amount: { amount: 741.4, currency: "SAR" },
    });

    expect(html).toContain("المبلغ المتبقي:");
    expect(html).toContain("741.40 SAR");
});
