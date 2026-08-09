import { buildSupplierDispatchPrintHtml } from "./supplierDispatchPrint";


test("supplier dispatch print contains the saved supplier file facts", () => {
    const html = buildSupplierDispatchPrintHtml({
        id: "dispatch-1",
        supplier_name: "مورد النقش",
        file_number: "PF-100",
        sent_by_name: "أحمد",
        piece_count: 2,
        sent_at: "2026-08-07T12:00:00Z",
        source_files: [{
            file_number: "PF-100",
            registered_at: "2026-08-07T12:00:00Z",
            cards: [{
                product_name: "سلسال الاسم",
                selected_image_url: "https://cdn.salla.sa/necklace.jpg",
                barcode_value: "a".repeat(32),
                order_number: "3001",
                quantity: 1,
                shipping_company: "سمسا",
                order_piece_count: 3,
                specifications: [{ name: "اللون", value: "ذهبي" }],
            }],
        }],
    });

    expect(html).toContain("ملف تجهيز المورد");
    expect(html).toContain("مورد النقش");
    expect(html).toContain("PF-100");
    expect(html).toContain('class="product-grid"');
    expect(html).toContain('class="product-card"');
    expect(html).toContain("grid-template-columns: repeat(3");
    expect(html).toContain("https://cdn.salla.sa/necklace.jpg");
    expect(html).toContain(`باركود ${"A".repeat(32)}`);
    expect(html).toContain("ط:3001");
    expect(html).toContain("2026/08/07");
    expect(html).toContain("الكمية: 1");
    expect(html).toContain("سمسا - 3");
    expect(html).toContain("اللون");
    expect(html).toContain("ذهبي");
    expect(html).not.toContain("سلسال الاسم");
    expect(html).not.toContain("<table>");
});


test("supplier dispatch print escapes customer specification text", () => {
    const html = buildSupplierDispatchPrintHtml({
        source_files: [{
            file_number: "PF-ESCAPE",
            cards: [{
                barcode_value: "b".repeat(32),
                specifications: [{ name: "النقش", value: "<script>alert(1)</script>" }],
            }],
        }],
    });

    expect(html).not.toContain("<script>alert(1)</script>");
    expect(html).toContain("&lt;script&gt;");
});


test("combined supplier print preserves a block for every preparation file", () => {
    const html = buildSupplierDispatchPrintHtml({
        id: "dispatch-2",
        supplier_file_number: "SF-20260808-ABC123",
        supplier_name: "مورد الذهب",
        piece_count: 3,
        source_files: [
            {
                file_number: "PF-100",
                registered_at: "2026-08-08T08:00:00Z",
                cards: [
                    { order_number: "3001", barcode_value: "1".repeat(32) },
                    { order_number: "3002", barcode_value: "2".repeat(32) },
                ],
            },
            {
                file_number: "PF-101",
                registered_at: "2026-08-08T09:00:00Z",
                cards: [{ order_number: "3003", barcode_value: "3".repeat(32) }],
            },
        ],
    });

    expect(html).toContain("ملف تجهيز المورد — مورد الذهب");
    expect(html).toContain("رقم ملف المورد:");
    expect(html).toContain("SF-20260808-ABC123");
    expect(html).toContain("ملف التجهيز: PF-100");
    expect(html).toContain("ملف التجهيز: PF-101");
    expect((html.match(/class="product-card"/g) || [])).toHaveLength(3);
    expect(html).toContain("ط:3001");
    expect(html).toContain("ط:3003");
});
