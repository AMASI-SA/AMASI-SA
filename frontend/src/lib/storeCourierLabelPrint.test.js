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


test("local QR keeps exact ID and prints purpose, original and only remaining collection", () => {
    const number="MZ-ABCDEF1234567890ABCDEF1234567890";
    const html=storeCourierLabelHtml({order_number:number,barcode_value:number,purpose_badge:"بدل",
        original_order_number:"276628330",remaining_amount:{amount:20,currency:"SAR"},total:{amount:60,currency:"SAR"}});
    expect(html).toContain('class="sheet local-order"');
    expect(html).toContain(`<div class="barcode-value">${number}</div>`);
    expect(html).toContain('الطلب الأصلي:'); expect(html).toContain('بدل');
    expect(html).toContain('20.00 SAR'); expect(html).not.toContain('60.00 SAR');
});
test("special badges are escaped and not inferred for paid Salla gifts", () => {
    const base={purpose_badge:'<script>alert(1)</script>',original_order_number:'<img onerror=x>'};
    const local=storeCourierLabelHtml({...base,order_number:'MZ-ABCDEF1234567890ABCDEF1234567890'});
    expect(local).not.toContain('<script>'); expect(local).not.toContain('<img onerror');
    const salla=storeCourierLabelHtml({...base,order_number:'276628330'});
    expect(salla).not.toContain('class="purpose"'); expect(salla).not.toContain('الطلب الأصلي:');
});

test("store courier label shows the COD balance with two decimals", () => {
    const html = storeCourierLabelHtml({
        order_number: "test-order",
        remaining_amount: { amount: 741.4, currency: "SAR" },
    });

    expect(html).toContain("المبلغ المتبقي:");
    expect(html).toContain("741.40 SAR");
});
