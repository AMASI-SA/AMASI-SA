import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";

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

const instructionFixture = [
    { id: "ack", target_stages: ["assembly_labeling"], enforcement: "acknowledgement_required", note: "Read this instruction" },
    { id: "complete", target_stages: ["assembly_labeling"], enforcement: "completion_required", required_action: "upload_photos_and_result", note: "Keep this history visible" },
];

test("delivered search retains instruction text without acknowledgement, completion or upload controls", () => {
    const markup = renderToStaticMarkup(<AssemblyProductCard piece={{
        piece_id: "delivered-piece", can_mark_ready: false,
        assembly_blocker_code: "assembly_order_delivered",
        customer_service_instructions: instructionFixture,
    }} />);
    expect(markup).toContain("Read this instruction");
    expect(markup).toContain("Keep this history visible");
    expect(markup).not.toContain("اطلعت على التعليمات");
    expect(markup).not.toContain("تنفيذ المطلوب وفتح المرحلة");
    expect(markup).not.toContain('type="file"');
    expect(markup).not.toContain("<textarea");
});

test("non-delivered assembly retains instruction acknowledgement, completion and evidence controls", () => {
    const markup = renderToStaticMarkup(<AssemblyProductCard piece={{
        piece_id: "open-piece", can_mark_ready: true,
        customer_service_instructions: instructionFixture,
    }} />);
    expect(markup).toContain("اطلعت على التعليمات");
    expect(markup).toContain("تنفيذ المطلوب وفتح المرحلة");
    expect(markup).toContain('type="file"');
    expect(markup).toContain("<textarea");
});

test("delivered assembly product is read only and shows no ready action", () => {
    const markup = renderToStaticMarkup(
        <AssemblyProductCard piece={{
            piece_id: "delivered-piece", product_name: "منتج",
            can_mark_ready: false, assembly_ready: false,
            assembly_blocker_code: "assembly_order_delivered",
        }} />,
    );
    expect(markup).toContain("تم توصيل الطلب في سلة؛ متاح للعرض فقط");
    expect(markup).not.toContain('data-testid="mark-assembly-piece-ready"');
    expect(markup).not.toContain('data-testid="mark-assembly-piece-ready-frozen"');
});

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

test("an unreceived supplier product remains visible with custody trace and a frozen ready button", () => {
    const markup = renderToStaticMarkup(
        <AssemblyProductCard
            piece={{
                piece_id: "pending-1",
                product_name: "لوحة جدارية",
                current_stage_label: "لدى المورد",
                route_steps: [{ label: "لدى المورد", actor_name: "مورد الرياض" }],
                can_mark_ready: false,
                assembly_blocker_code: "assembly_piece_supplier_receipt_required",
            }}
            busy={false}
            onReady={() => {}}
        />,
    );
    expect(markup).toContain("لوحة جدارية");
    expect(markup).toContain("المرحلة الحالية: لدى المورد");
    expect(markup).toContain("مسار تتبع المنتج");
    expect(markup).toContain("مورد الرياض");
    expect(markup).toContain("استلم المنتج من المورد أولًا، ثم من موظف التجهيز");
    expect(markup).toContain('data-testid="mark-assembly-piece-ready-frozen"');
    expect(markup).toContain('aria-disabled="true"');
    expect(markup).not.toContain('data-testid="mark-assembly-piece-ready"');
});

test("pressing frozen ready reports the unfinished product without submitting readiness", () => {
    const previousActEnvironment = globalThis.IS_REACT_ACT_ENVIRONMENT;
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    const root = createRoot(container);
    const onBlocked = jest.fn();
    const onReady = jest.fn();
    const piece = {
        piece_id: "pending-1",
        product_name: "لوحة جدارية",
        can_mark_ready: false,
        assembly_blocker_code: "assembly_piece_supplier_receipt_required",
    };
    act(() => root.render(<AssemblyProductCard piece={piece} busy={false} onReady={onReady} onBlocked={onBlocked} />));
    act(() => container.querySelector('[data-testid="mark-assembly-piece-ready-frozen"]').click());
    expect(container.querySelector('[role="alert"]').textContent).toContain("المنتج لم يجهز بعد");
    expect(onBlocked).toHaveBeenCalledWith(piece);
    expect(onReady).not.toHaveBeenCalled();
    act(() => root.unmount());
    globalThis.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
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
