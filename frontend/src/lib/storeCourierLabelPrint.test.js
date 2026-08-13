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
    expect(html).toContain("سلسال × 1");
});
