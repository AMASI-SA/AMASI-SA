import { renderToStaticMarkup } from "react-dom/server";

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

jest.mock("../../services/fulfillmentV2", () => ({
    confirmCompletedCarrierLabelPrint: jest.fn(),
    listReadyToShipOrders: jest.fn(() => Promise.resolve({ items: [], permissions: {} })),
    issueCompletedOrderCarrierLabel: jest.fn(),
}));

jest.mock("../../services/preparationWorkService", () => ({
    markAssemblyPieceReady: jest.fn(),
    newAssemblyReadyRequestId: () => "assembly-ready:test-1",
    searchAssemblyOrder: jest.fn(),
}));

jest.mock("./PreparationEmployeeReceivingWorkspace", () => ({
    CameraScanner: () => <div data-testid="camera-scanner" />,
}));

import ReadyToShipOrders, {
    AssemblyProductCard,
    CompletedAssemblyOrderCard,
} from "./ReadyToShipOrders";

test("assembly page starts with an obvious order search camera and ready queue", () => {
    const markup = renderToStaticMarkup(<ReadyToShipOrders />);

    expect(markup).toContain("التجميع والعنونة");
    expect(markup).toContain("ابحث برقم الطلب أو المنتج");
    expect(markup).toContain('placeholder="رقم الطلب أو باركود المنتج"');
    expect(markup).toContain('aria-label="فتح الكاميرا للبحث عن منتج التجميع"');
    expect(markup).toContain("الطلبات الجاهزة للتجميع");
    expect(markup).toContain("تم التنفيذ");
    expect(markup).not.toContain("دفعات الطباعة والتسليم السابقة");
    expect(markup).not.toContain("سبب إعادة الطباعة");
});

test("assembly product shows full customer information and one ready button", () => {
    const markup = renderToStaticMarkup(
        <AssemblyProductCard
            piece={{
                piece_id: "piece-1",
                unit_index: 1,
                product_name: "سلسال بالاسم",
                sku: "AMS-1",
                image_url: "https://cdn.example.com/product.jpg",
                responsible_employee_name: "عرفات",
                search_match: true,
                can_mark_ready: true,
                specifications: [
                    { name: "الاسم", value: "سارة" },
                    { name: "اللون", value: "ذهبي" },
                ],
                services: [{ name: "كتابة الاسم", status: "completed" }],
            }}
            busy={false}
            onReady={() => {}}
        />,
    );

    expect(markup).toContain("هذا هو المنتج الذي تم تصويره");
    expect(markup).toContain("سلسال بالاسم");
    expect(markup).toContain("عرفات");
    expect(markup).toContain("سارة");
    expect(markup).toContain("ذهبي");
    expect(markup).toContain("كتابة الاسم");
    expect(markup).toContain(">جاهز<");
    expect(markup).toContain('src="https://cdn.example.com/product.jpg"');
});

test("a ready product no longer exposes a second ready action", () => {
    const markup = renderToStaticMarkup(
        <AssemblyProductCard
            piece={{
                piece_id: "piece-1",
                product_name: "منتج مكتمل",
                assembly_ready: true,
                can_mark_ready: false,
                specifications: [],
            }}
            busy={false}
            onReady={() => {}}
        />,
    );

    expect(markup).toContain("تم — جاهز");
    expect(markup).not.toContain('data-testid="mark-assembly-piece-ready"');
});

test("store courier assembly card prints then confirms the attached QR", () => {
    const markup = renderToStaticMarkup(
        <CompletedAssemblyOrderCard
            orderNumber="276628330"
            carrierLabel={{
                ready: true,
                label_type: "store_courier",
                print_data: {
                    order_number: "276628330",
                    qr_code: "data:image/svg+xml;base64,QR",
                },
            }}
            canConfirmPrint
            onConfirmPrint={() => {}}
            onIssue={() => {}}
        />,
    );

    expect(markup).toContain("طباعة بوليصة مندوب المتجر");
    expect(markup).toContain("تأكيد الطباعة واللصق بتصوير QR");
    expect(markup).toContain(
        'data-testid="assembly-confirm-carrier-label-print"',
    );
});

test("completed order search shows products and shipment as read-only history", () => {
    const markup = renderToStaticMarkup(
        <CompletedAssemblyOrderCard
            orderNumber="276628330"
            historyOnly
            carrierLabel={{
                ready: true,
                print_confirmed: true,
                label_type: "store_courier",
                shipment_state: "assigned_waiting_pickup",
                store_courier_assignee_name: "مندوب الرياض",
                print_data: {
                    order_number: "276628330",
                    qr_code: "data:image/svg+xml;base64,QR",
                },
            }}
            canConfirmPrint
            onConfirmPrint={() => {}}
            onIssue={() => {}}
        />,
    );

    expect(markup).toContain("سجل الطلب");
    expect(markup).toContain("المنتجات وبيانات الشحنة أدناه للعرض فقط");
    expect(markup).toContain('data-testid="assembly-shipment-read-only"');
    expect(markup).toContain("مندوب المتجر");
    expect(markup).toContain("مندوب الرياض");
    expect(markup).not.toContain('data-testid="assembly-print-store-courier-label"');
    expect(markup).not.toContain('data-testid="assembly-confirm-carrier-label-print"');
    expect(markup).not.toContain('data-testid="assembly-issue-carrier-label"');
});
