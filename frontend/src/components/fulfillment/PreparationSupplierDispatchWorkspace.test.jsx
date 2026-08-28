import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

const mockGetPreparationSupplierWorkspace = jest.fn();
const mockSendPreparationPiecesToSupplier = jest.fn();

jest.mock("../../services/preparationSupplierDispatch", () => ({
    getPreparationSupplierWorkspace: (...args) => mockGetPreparationSupplierWorkspace(...args),
    getUnassignedPreparationPieces: jest.fn(),
    newPreparationDispatchRequestId: jest.fn(() => "supplier-dispatch:test-1"),
    reassignPreparationPieces: jest.fn(),
    rejectPreparationPieces: jest.fn(),
    sendPreparationPiecesToSupplier: (...args) => mockSendPreparationPiecesToSupplier(...args),
}));

jest.mock("./SupplierReceivingWorkspace", () => () => (
    <div data-testid="supplier-receiving-workspace">فاتورة المورد داخل إدارة منتجاتي</div>
));

import PreparationSupplierDispatchWorkspace, {
    applySupplierDispatchToWorkspaceData,
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


test("piece-grain cards stay independent and always select one physical piece", () => {
    expect(dispatchSelections(
        [
            { group_key: "piece:piece-1", piece_id: "piece-1", available_quantity: 1 },
            { group_key: "piece:piece-2", piece_id: "piece-2", available_quantity: 1 },
        ],
        {
            "piece:piece-1": 9,
            "piece:piece-2": 1,
        },
    )).toEqual([
        { group_key: "piece:piece-1", quantity: 1 },
        { group_key: "piece:piece-2", quantity: 1 },
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


test("a saved supplier dispatch immediately removes moved products from waiting review", () => {
    const updated = applySupplierDispatchToWorkspaceData({
        summary: {
            available_to_send: 3,
            sent: 0,
            waiting_review_pieces: 3,
            in_progress_pieces: 0,
        },
        files: [{
            file_number: "PF-100",
            available_quantity: 3,
            sent_quantity: 0,
            products: [
                { group_key: "product:1", available_quantity: 1, sent_quantity: 0 },
                { group_key: "product:2", available_quantity: 2, sent_quantity: 0 },
            ],
        }],
    }, [{
        file_number: "PF-100",
        selections: [{ group_key: "product:1", quantity: 1 }],
    }], {
        completed_source_file_numbers: [],
    });

    expect(updated.files[0].available_quantity).toBe(2);
    expect(updated.files[0].sent_quantity).toBe(1);
    expect(updated.files[0].products[0].available_quantity).toBe(0);
    expect(updated.files[0].products[0].sent_quantity).toBe(1);
    expect(updated.summary.waiting_review_pieces).toBe(2);
    expect(updated.summary.in_progress_pieces).toBe(1);
});


test("saving a supplier file does not leave the page blocked while background refresh waits", async () => {
    const initialData = {
        summary: { available_to_send: 1, waiting_review_pieces: 1 },
        suppliers: [{ id: "supplier-1", company_name: "مورد النقش" }],
        supplier_accounts: [],
        files: [{
            file_number: "PF-100",
            registered_at: "2026-08-08T08:00:00Z",
            available_quantity: 1,
            sent_quantity: 0,
            products: [{
                group_key: "product:1",
                product_name: "قطعة يجب أن تختفي",
                available_quantity: 1,
                sent_quantity: 0,
                services: [],
            }],
        }],
    };
    mockGetPreparationSupplierWorkspace
        .mockResolvedValueOnce(initialData)
        .mockImplementation(() => new Promise(() => {}));
    mockSendPreparationPiecesToSupplier.mockResolvedValue({
        dispatch: {
            id: "dispatch-1",
            supplier_name: "مورد النقش",
            supplier_file_number: "PF-100",
            completed_source_file_numbers: ["PF-100"],
            source_files: [{ file_number: "PF-100", cards: [] }],
        },
    });
    const printWindow = {
        document: {
            open: jest.fn(),
            write: jest.fn(),
            close: jest.fn(),
            title: "",
        },
        focus: jest.fn(),
        print: jest.fn(),
        close: jest.fn(),
    };
    const openSpy = jest.spyOn(window, "open").mockReturnValue(printWindow);
    const onDataChanged = jest.fn(() => new Promise(() => {}));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;

    try {
        await act(async () => {
            root.render(
                <PreparationSupplierDispatchWorkspace
                    view="waiting-review"
                    onDataChanged={onDataChanged}
                />,
            );
            await new Promise((resolve) => window.setTimeout(resolve, 20));
        });

        await act(async () => {
            container.querySelector('[data-testid="dispatch-product-selector"]').click();
            const supplierSelect = container.querySelector("select");
            supplierSelect.value = "supplier-1";
            supplierSelect.dispatchEvent(new Event("change", { bubbles: true }));
        });

        const saveButton = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("حفظ وطباعة ملف المورد"));
        await act(async () => {
            saveButton.click();
            await new Promise((resolve) => window.setTimeout(resolve, 20));
        });

        expect(mockSendPreparationPiecesToSupplier).toHaveBeenCalled();
        expect(container.textContent).not.toContain("قطعة يجب أن تختفي");
        expect(container.textContent).toContain("لا توجد منتجات بانتظار الإرسال للمورد");
        expect(container.textContent).not.toContain("جارٍ تحميل إدارة منتجاتي");
        expect(onDataChanged).toHaveBeenCalled();
    } finally {
        await act(async () => root.unmount());
        container.remove();
        openSpy.mockRestore();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
        jest.clearAllMocks();
    }
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


test("waiting review renders one independent card per physical piece", () => {
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
                products: [
                    {
                        group_key: "piece:piece-1",
                        piece_id: "piece-1",
                        product_name: "سلسال الاسم",
                        sku: "AMS13067",
                        order_numbers: ["3001"],
                        selected_image_url: "https://example.test/product.jpg",
                        available_quantity: 1,
                        services: [{ service_id: "engrave", service_name: "نحت", status: "pending" }],
                    },
                    {
                        group_key: "piece:piece-2",
                        piece_id: "piece-2",
                        product_name: "سلسال الاسم",
                        sku: "AMS13067",
                        order_numbers: ["3002"],
                        selected_image_url: "https://example.test/product.jpg",
                        available_quantity: 1,
                        services: [{ service_id: "engrave", service_name: "نحت", status: "pending" }],
                    },
                ],
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
    expect(markup.match(/data-testid="dispatch-product-selector"/g)).toHaveLength(2);
    expect(markup).toContain('data-piece-id="piece-1"');
    expect(markup).toContain('data-piece-id="piece-2"');
    expect(markup).toContain("قطعة واحدة");
    expect(markup).toContain("طلب 3001");
    expect(markup).toContain("طلب 3002");
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
