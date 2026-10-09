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
    refreshCompletedOrderCarrierLabel: jest.fn(),
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

test("non-executable order keeps its product and frozen ready button without submitting", () => {
    const previous = globalThis.IS_REACT_ACT_ENVIRONMENT;
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    const root = createRoot(container);
    const onReady = jest.fn();
    const piece = { piece_id: "status-blocked", product_name: "منتج للعرض",
        can_mark_ready: false, assembly_blocker_code: "assembly_order_not_in_progress" };
    act(() => root.render(<AssemblyProductCard piece={piece} busy={false} onReady={onReady} />));
    expect(container.textContent).toContain("منتج للعرض");
    const frozen = container.querySelector('[data-testid="mark-assembly-piece-ready-frozen"]');
    expect(frozen.getAttribute("aria-disabled")).toBe("true");
    act(() => frozen.click());
    expect(container.querySelector('[role="alert"]').textContent).toContain("الطلب غير قيد التنفيذ في سلة");
    expect(onReady).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mark-assembly-piece-ready"]')).toBeNull();
    act(() => root.unmount());
    globalThis.IS_REACT_ACT_ENVIRONMENT = previous;
});

test("store courier assembly card prints then confirms the attached QR", () => {
    const markup = renderToStaticMarkup(
        <CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint
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
        <CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint
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
    expect(markup).toContain('data-testid="assembly-print-store-courier-label"');
    expect(markup).not.toContain('data-testid="assembly-confirm-carrier-label-print"');
    expect(markup).not.toContain('data-testid="assembly-issue-carrier-label"');
});


jest.mock("../../lib/storeCourierLabelPrint", () => ({ printStoreCourierLabel: jest.fn(() => true) }));
const { refreshCompletedOrderCarrierLabel, issueCompletedOrderCarrierLabel, confirmCompletedCarrierLabelPrint } = require("../../services/fulfillmentV2");
const { printStoreCourierLabel } = require("../../lib/storeCourierLabelPrint");

describe("assembly opens current labels without issuing or confirming", () => {
    let container, root, popup;
    beforeEach(() => {
        jest.clearAllMocks();
        globalThis.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        root = createRoot(container);
        popup = { opener: {}, location: { replace: jest.fn() }, close: jest.fn() };
        jest.spyOn(window, "open").mockReturnValue(popup);
    });
    afterEach(() => { act(() => root.unmount()); jest.restoreAllMocks(); });

    test.each([
        [{ ready: false, message: "shipping_snapshot_changed" }, "assembly-issue-carrier-label"],
        [{ ready: true, label_url: "https://labels.test/old.pdf" }, "assembly-download-official-carrier-label"],
        [{ ready: true, label_type: "store_courier", print_data: { qr_code: "old" } }, "assembly-print-store-courier-label"],
    ])("all open buttons use current read even with saved identity %j", async (saved, button) => {
        refreshCompletedOrderCarrierLabel.mockResolvedValue({ ready: true, label_url: "https://labels.test/current.pdf" });
        act(() => root.render(<CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint orderNumber="synthetic-42" carrierLabel={saved} />));
        expect(container.textContent).not.toContain("shipping_snapshot_changed");
        await act(async () => container.querySelector(`[data-testid="${button}"]`).click());
        expect(refreshCompletedOrderCarrierLabel).toHaveBeenCalledWith("synthetic-42");
        expect(popup.location.replace).toHaveBeenCalledWith("https://labels.test/current.pdf");
        expect(issueCompletedOrderCarrierLabel).not.toHaveBeenCalled();
        expect(confirmCompletedCarrierLabelPrint).not.toHaveBeenCalled();
    });

    test("current courier document uses legacy formatter, never saved payload", async () => {
        const print_data = { qr_code: "current", order_number: "synthetic-42" };
        refreshCompletedOrderCarrierLabel.mockResolvedValue({ ready: true, label_type: "store_courier", print_data });
        act(() => root.render(<CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint orderNumber="synthetic-42" carrierLabel={{ ready: false }} />));
        await act(async () => container.querySelector('[data-testid="assembly-issue-carrier-label"]').click());
        expect(printStoreCourierLabel).toHaveBeenCalledWith(popup, print_data);
        expect(issueCompletedOrderCarrierLabel).not.toHaveBeenCalled();
    });

    test("missing current label cannot open saved URL", async () => {
        refreshCompletedOrderCarrierLabel.mockResolvedValue({ ready: false, message: "البوليصة الحالية غير متاحة" });
        act(() => root.render(<CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint orderNumber="synthetic-42" carrierLabel={{ ready: true, label_url: "https://labels.test/old.pdf" }} />));
        await act(async () => container.querySelector('[data-testid="assembly-download-official-carrier-label"]').click());
        expect(window.open).not.toHaveBeenCalled();
        expect(container.querySelector('[role="alert"]').textContent).toBe("البوليصة الحالية غير متاحة");
    });

    test("permission denied never reads or opens", async () => {
        act(() => root.render(<CompletedAssemblyOrderCard assemblyCompletionConfirmed orderNumber="synthetic-42" carrierLabel={{}} canPrint={false} canConfirmPrint={false} />));
        await act(async () => container.querySelector('[data-testid="assembly-issue-carrier-label"]').click());
        expect(refreshCompletedOrderCarrierLabel).not.toHaveBeenCalled();
        expect(window.open).not.toHaveBeenCalled();
    });

    test("late result for another order is not opened", async () => {
        let resolve;
        refreshCompletedOrderCarrierLabel.mockReturnValue(new Promise((done) => { resolve = done; }));
        act(() => root.render(<CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint orderNumber="synthetic-42" carrierLabel={{}} />));
        act(() => container.querySelector('[data-testid="assembly-issue-carrier-label"]').click());
        act(() => root.render(<CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint orderNumber="synthetic-43" carrierLabel={{}} />));
        await act(async () => resolve({ ready: true, label_url: "https://labels.test/wrong-order.pdf" }));
        expect(window.open).not.toHaveBeenCalled();
    });
});


describe("assembly completion is the independent print boundary", () => {
    let container, root, popup;
    const { listReadyToShipOrders } = require("../../services/fulfillmentV2");
    const { searchAssemblyOrder } = require("../../services/preparationWorkService");
    beforeEach(() => {
        jest.clearAllMocks();
        globalThis.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        root = createRoot(container);
        popup = { opener: {}, location: { replace: jest.fn() }, close: jest.fn() };
        jest.spyOn(window, "open").mockReturnValue(popup);
        listReadyToShipOrders.mockResolvedValue({
            permissions: { can_print: true },
            items: [{ order_number: "synthetic-42", ready_to_ship_source: "preparation_receipt" }],
        });
        refreshCompletedOrderCarrierLabel.mockResolvedValue({ ready: true, label_url: "https://labels.test/current.pdf" });
    });
    afterEach(() => { act(() => root.unmount()); jest.restoreAllMocks(); });
    async function search(data) {
        searchAssemblyOrder.mockResolvedValue({ order_number: "synthetic-42", pieces: [], ...data });
        await act(async () => root.render(<ReadyToShipOrders />));
        await act(async () => container.querySelector("article button").click());
    }

    test.each([undefined, false, "true"])("completion %s cannot be inferred from ready totals or delivered status", async (proof) => {
        await search({ assembly_completion_confirmed: proof, status: "delivered", stage: "completed", summary: { all_ready: true }, progress: { order_completed: true } });
        expect(container.querySelector('[data-testid="assembly-order-completed"]')).toBeNull();
        expect(refreshCompletedOrderCarrierLabel).not.toHaveBeenCalled();
        expect(window.open).not.toHaveBeenCalled();
    });

    test.each([
        ["in_progress", "completed"], ["shipped", "delivering"], ["delivered", "delivered"],
    ])("confirmed assembly permits reprint for Salla %s and local stage %s", async (status, stage) => {
        await search({ assembly_completion_confirmed: true, status, stage, history_only: true, carrier_label: { ready: false, print_confirmed: true } });
        await act(async () => container.querySelector('[data-testid="assembly-issue-carrier-label"]').click());
        expect(refreshCompletedOrderCarrierLabel).toHaveBeenCalledWith("synthetic-42");
        expect(popup.location.replace).toHaveBeenCalledWith("https://labels.test/current.pdf");
        expect(container.querySelector('[data-testid="assembly-confirm-carrier-label-print"]')).toBeNull();
        expect(issueCompletedOrderCarrierLabel).not.toHaveBeenCalled();
        expect(confirmCompletedCarrierLabelPrint).not.toHaveBeenCalled();
    });

    test("completion alone does not broaden print confirmation", async () => {
        await search({ assembly_completion_confirmed: true, carrier_label: { ready: true, label_url: "old" } });
        expect(container.querySelector('[data-testid="assembly-confirm-carrier-label-print"]').disabled).toBe(true);
        await act(async () => container.querySelector('[data-testid="assembly-download-official-carrier-label"]').click());
        expect(refreshCompletedOrderCarrierLabel).toHaveBeenCalledTimes(1);
        expect(confirmCompletedCarrierLabelPrint).not.toHaveBeenCalled();
    });

    test("revoked completion prevents an in-flight response opening", async () => {
        let resolve;
        refreshCompletedOrderCarrierLabel.mockReturnValue(new Promise((done) => { resolve = done; }));
        act(() => root.render(<CompletedAssemblyOrderCard assemblyCompletionConfirmed canPrint orderNumber="synthetic-42" carrierLabel={{}} />));
        act(() => container.querySelector('[data-testid="assembly-issue-carrier-label"]').click());
        act(() => root.render(<CompletedAssemblyOrderCard canPrint orderNumber="synthetic-42" carrierLabel={{}} />));
        await act(async () => resolve({ ready: true, label_url: "https://labels.test/current.pdf" }));
        expect(window.open).not.toHaveBeenCalled();
        expect(container.innerHTML).toBe("");
    });
});
