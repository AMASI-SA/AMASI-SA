import { buildSupplierDispatchPrintHtml } from "./supplierDispatchPrint";


test("supplier dispatch print contains the saved supplier file facts", () => {
    const html = buildSupplierDispatchPrintHtml({
        id: "dispatch-1",
        supplier_name: "مورد النقش",
        file_number: "PF-100",
        sent_by_name: "أحمد",
        piece_count: 2,
        sent_at: "2026-08-07T12:00:00Z",
        lines: [{
            product_name: "سلسال الاسم",
            sku: "NECK-1",
            quantity: 2,
            order_numbers: ["3001", "3002"],
            services: [{ service_name: "نحت", status: "pending" }],
        }],
    });

    expect(html).toContain("ملف تجهيز المورد");
    expect(html).toContain("مورد النقش");
    expect(html).toContain("PF-100");
    expect(html).toContain("سلسال الاسم");
    expect(html).toContain("3001، 3002");
    expect(html).toContain("نحت");
});


test("supplier dispatch print escapes product text", () => {
    const html = buildSupplierDispatchPrintHtml({
        lines: [{ product_name: "<script>alert(1)</script>", quantity: 1 }],
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
                lines: [{ product_name: "سلسال", quantity: 2 }],
            },
            {
                file_number: "PF-101",
                registered_at: "2026-08-08T09:00:00Z",
                lines: [{ product_name: "خاتم", quantity: 1 }],
            },
        ],
    });

    expect(html).toContain("ملف تجهيز المورد — مورد الذهب");
    expect(html).toContain("رقم ملف المورد:");
    expect(html).toContain("SF-20260808-ABC123");
    expect(html).toContain("ملف التجهيز: PF-100");
    expect(html).toContain("ملف التجهيز: PF-101");
    expect(html).toContain("سلسال");
    expect(html).toContain("خاتم");
});
