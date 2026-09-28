import { storeCourierLabelHtml } from "./storeCourierLabelPrint";


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
