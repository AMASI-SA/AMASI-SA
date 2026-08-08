import { renderToStaticMarkup } from "react-dom/server";

jest.mock("./SupplierReceivingWorkspace", () => () => (
    <div data-testid="supplier-receiving-workspace">فاتورة المورد داخل إدارة منتجاتي</div>
));

import PreparationSupplierDispatchWorkspace, {
    dispatchSelections,
    dispatchSelectionState,
    MyProductsOverview,
    OrderBarcodeCameraScanner,
    orderSearchValueFromBarcode,
    productImageUrl,
    ReceivedView,
    selectedFileDispatches,
    supplierDispatchForPrint,
    toggledDispatchQuantity,
    WaitingReviewView,
} from "./PreparationSupplierDispatchWorkspace";


test("dispatch selection clamps quantities to the employee available pieces", () => {
    expect(dispatchSelections(
        [
            { group_key: "product:1", available_quantity: 3 },
            { group_key: "product:2", available_quantity: 1 },
        ],
        { "product:1": 9, "product:2": 0 },
    )).toEqual([
        { group_key: "product:1", quantity: 3 },
    ]);
});


test("selecting a product starts with every available piece and toggles back to hidden", () => {
    const product = { product_name: "سلسال الاسم", available_quantity: 20 };

    expect(toggledDispatchQuantity(product, 0)).toBe(20);
    expect(dispatchSelectionState(product, 20)).toBe("full");
    expect(dispatchSelectionState(product, 10)).toBe("partial");
    expect(toggledDispatchQuantity(product, 10)).toBe(0);
});


test("one supplier file keeps selections grouped by their source preparation file", () => {
    expect(selectedFileDispatches([
        {
            file_number: "PF-100",
            products: [{ group_key: "product:1", available_quantity: 2 }],
        },
        {
            file_number: "PF-101",
            products: [{ group_key: "product:1", available_quantity: 3 }],
        },
    ], {
        "PF-100": { "product:1": 2 },
        "PF-101": { "product:1": 1 },
    })).toEqual([
        { file_number: "PF-100", selections: [{ group_key: "product:1", quantity: 2 }] },
        { file_number: "PF-101", selections: [{ group_key: "product:1", quantity: 1 }] },
    ]);
});


test("print data keeps the selected supplier name when an API response omits it", () => {
    expect(supplierDispatchForPrint(
        { id: "dispatch-1" },
        [{ id: "supplier-1", company_name: "مورد النقش" }],
        "supplier-1",
    ).supplier_name).toBe("مورد النقش");
});


test("product image prefers manual choice then resolved Salla image then source image", () => {
    expect(productImageUrl({
        selected_image_url: "https://example.test/manual.jpg",
        resolved_image_url: "https://cdn.salla.sa/resolved.jpg",
        image_url: "https://cdn.salla.sa/source.jpg",
    })).toBe("https://example.test/manual.jpg");
    expect(productImageUrl({
        selected_image_url: null,
        resolved_image_url: "https://cdn.salla.sa/AMS11542.jpg",
        image_url: "https://cdn.salla.sa/source.jpg",
    })).toBe("https://cdn.salla.sa/AMS11542.jpg");
    expect(productImageUrl({
        selected_image_url: null,
        resolved_image_url: null,
        image_url: "https://cdn.salla.sa/source.jpg",
    })).toBe("https://cdn.salla.sa/source.jpg");
});


test("order barcode search extracts the order number and exposes a real camera dialog", () => {
    expect(orderSearchValueFromBarcode("ORDER:276936126")).toBe("276936126");
    expect(orderSearchValueFromBarcode("276936126")).toBe("276936126");

    const markup = renderToStaticMarkup(
        <OrderBarcodeCameraScanner onDetected={() => {}} onClose={() => {}} />,
    );
    expect(markup).toContain('data-testid="my-products-order-camera-dialog"');
    expect(markup).toContain("مسح باركود الطلب");
});


test("my products is the default employee in-progress window", () => {
    const markup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace />,
    );

    expect(markup).toContain("جارٍ تحميل إدارة منتجاتي");
});


test("manager unassigned queue remains independent from employee products", () => {
    const employeeMarkup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace view="my-products" />,
    );
    const managerMarkup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace view="unassigned" />,
    );

    expect(employeeMarkup).toContain("جارٍ تحميل إدارة منتجاتي");
    expect(managerMarkup).toContain("جارٍ تحميل المنتجات غير المسندة");
});


test("my products overview uses the approved four account-wide counters", () => {
    const markup = renderToStaticMarkup(<MyProductsOverview data={{
        summary: {
            waiting_review_pieces: 22,
            in_progress_pieces: 73,
            received_pieces_awaiting_branch_handoff: 33,
            waiting_review_products: 21,
            in_progress_products: 74,
            received_orders_awaiting_branch_handoff: 33,
            total_assigned_pieces: 128,
        },
        files: [{
            file_number: "PF-024",
            file_title: "ملف أحمد 024",
            piece_count: 24,
            received_quantity: 9,
            sent_quantity: 4,
            ready_quantity: 0,
            products: [],
        }],
        supplier_accounts: [{
            supplier_id: "supplier-1",
            supplier_name: "مؤسسة النور",
            sent_quantity: 32,
            ready_quantity: 0,
            received_quantity: 0,
            dispatches: [{ id: "invoice-1" }],
        }],
    }} onOpen={() => {}} />);

    expect(markup).toContain("بانتظار المراجعة");
    expect(markup).toContain("قيد التنفيذ");
    expect(markup).toContain("تم الاستلام");
    expect(markup).toContain("إجمالي القطع المسندة");
    ["22", "73", "33", "128"].forEach((value) => expect(markup).toContain(`>${value}<`));
    expect(markup).toContain("ملخص العمل العام");
    expect(markup).not.toContain("ملخص العمل اليوم");
    expect(markup).toContain("إدارة المنتجات المسندة لك ومتابعة الموردين");
    expect(markup).toContain("استلام من المورد");
    expect(markup).toContain("البحث برقم الطلب");
    expect(markup).toContain('data-testid="my-products-order-camera-button"');
    expect(markup).toContain("فواتير الموردين");
    expect(markup).not.toContain('/fulfillment-v2?stage=preparation');
    expect(markup).toContain("آخر ملفات التجهيز");
    expect(markup).toContain("حالة الموردين");
    expect(markup).toContain("ملف أحمد 024");
    expect(markup).toContain("مؤسسة النور");
    expect(markup).toContain("grid-cols-3");
});


test("received card renders assigned received products even without a supplier account row", () => {
    const markup = renderToStaticMarkup(<ReceivedView
        data={{
            summary: {
                received_orders_awaiting_branch_handoff: 1,
                received_pieces_awaiting_branch_handoff: 1,
            },
            supplier_accounts: [],
            files: [{
                file_number: "PF-RECEIVED-1",
                products: [{
                    group_key: "product:received",
                    product_name: "سلسال جاهز للاستلام",
                    received_quantity: 1,
                    resolved_image_url: "https://cdn.salla.sa/received.jpg",
                }],
            }],
        }}
        loading={false}
        error=""
        onRefresh={() => {}}
        onBack={() => {}}
    />);

    expect(markup).toContain("سلسال جاهز للاستلام");
    expect(markup).toContain("قطعة مستلمة");
    expect(markup).not.toContain("لا توجد قطع مستلمة");
});


test("waiting review renders two product cards per mobile row and mandatory return action", () => {
    const markup = renderToStaticMarkup(<WaitingReviewView
        data={{
            summary: {},
            suppliers: [{ id: "supplier-1", company_name: "مورد النقش" }],
            files: [{
                file_number: "PF-100",
                file_title: "ملف أحمد",
                registered_at: "2026-08-08T08:00:00Z",
                available_quantity: 2,
                sent_quantity: 0,
                products: [{
                    group_key: "product:1",
                    product_name: "سلسال الاسم",
                    selected_image_url: "https://example.test/product.jpg",
                    available_quantity: 2,
                    services: [{ service_id: "engrave", service_name: "نحت", status: "pending" }],
                }],
            }],
        }}
        loading={false}
        error=""
        onRefresh={() => {}}
        onChanged={async () => {}}
        onBack={() => {}}
    />);

    expect(markup).toContain("grid-cols-2");
    expect(markup).toContain("lg:grid-cols-4");
    expect(markup).toContain("سلسال الاسم");
    expect(markup).toContain("object-contain");
    expect(markup).toContain('data-testid="dispatch-product-selector"');
    expect(markup).toContain('data-selection-state="unselected"');
    expect(markup).not.toContain('data-testid="dispatch-quantity-control"');
    expect(markup).not.toContain("اختيار كامل");
    expect(markup).not.toContain("اختيار جزئي");
    expect(markup).toContain("إرجاع الإسناد");
    expect(markup).toContain("خيارات سلسال الاسم");
    expect(markup).toContain("تاريخ الرفع");
    expect(markup).toContain('data-testid="multi-file-supplier-dispatch"');
    expect(markup).toContain("ملف مورد واحد");
    expect(markup).toContain("حفظ وطباعة ملف المورد");
    expect(markup).toContain("بانتظار المراجعة");
});


test("waiting review renders a resolved Salla image without a manual image choice", () => {
    const markup = renderToStaticMarkup(<WaitingReviewView
        data={{
            suppliers: [],
            files: [{
                file_number: "PF-11542",
                available_quantity: 1,
                products: [{
                    group_key: "product:AMS11542",
                    product_name: "كرت إهداء حسب الطلب",
                    selected_image_url: null,
                    resolved_image_url: "https://cdn.salla.sa/AMS11542.jpg",
                    available_quantity: 1,
                    services: [],
                }],
            }],
        }}
        loading={false}
        error=""
        onRefresh={() => {}}
        onChanged={async () => {}}
        onBack={() => {}}
    />);

    expect(markup).toContain('src="https://cdn.salla.sa/AMS11542.jpg"');
    expect(markup).toContain('data-testid="dispatch-product-image"');
});
