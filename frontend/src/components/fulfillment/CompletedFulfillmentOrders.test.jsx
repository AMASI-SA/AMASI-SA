import { createRoot } from "react-dom/client";
import { act } from "react";
import { renderToStaticMarkup } from "react-dom/server";

jest.mock("sonner", () => ({ toast: { success: jest.fn() } }));
jest.mock("../../services/fulfillmentV2", () => ({
    confirmCompletedCarrierLabelPrint: jest.fn(),
    issueCompletedOrderCarrierLabel: jest.fn(),
    listCarrierHandoffShipments: jest.fn(() => Promise.resolve({ items: [] })),
    listCompletedFulfillmentOrders: jest.fn(() => Promise.resolve({ items: [], permissions: {} })),
    refreshCompletedOrderCarrierLabel: jest.fn(),
    scanCarrierHandoffShipment: jest.fn(),
}));

import CompletedFulfillmentOrders, {
    CarrierLabelControl,
    savedCarrierSnapshot,
    openCurrentCarrierLabel,
    shippingScanFeedback,
} from "./CompletedFulfillmentOrders";
import { issueCompletedOrderCarrierLabel, listCompletedFulfillmentOrders, refreshCompletedOrderCarrierLabel } from "../../services/fulfillmentV2";
import { printStoreCourierLabel } from "../../lib/storeCourierLabelPrint";
jest.mock("../../lib/storeCourierLabelPrint", () => ({ printStoreCourierLabel: jest.fn(() => true) }));

import ShippingBarcodeScanner from "./ShippingBarcodeScanner";

const permissions = { can_print: true, can_confirm_print: true };

test("external courier exposes only the official provider label link", () => {
    const markup = renderToStaticMarkup(
        <CarrierLabelControl
            order={{
                order_number: "276628330",
                shipping_company: "iMile",
                carrierSnapshot: {
                    ready: true,
                    label_url: "https://carrier.example/label.pdf",
                    tracking_number: "IM123",
                    courier_name: "iMile",
                    order_status_completed: true,
                },
            }}
            permissions={permissions}
            busy={false}
            onIssue={() => {}}
            onConfirmPrint={() => {}}
        />,
    );

    expect(markup).toContain("تحميل بوليصة");
    expect(markup).toContain("iMile");
    expect(markup).not.toContain("https://carrier.example/label.pdf");
    expect(markup).toContain('data-testid="download-official-carrier-label"');
    expect(markup).not.toContain("مندوب المتجر");
});

test("store courier exposes the Mezan-designed printable label", () => {
    const markup = renderToStaticMarkup(
        <CarrierLabelControl
            order={{
                order_number: "1001",
                shipping_company: "مندوب المتجر",
                carrierSnapshot: {
                    ready: true,
                    label_type: "store_courier",
                    order_status_completed: true,
                    print_data: {
                        order_number: "1001",
                        qr_code: "data:image/svg+xml;base64,QR",
                    },
                },
            }}
            permissions={permissions}
            busy={false}
            onIssue={() => {}}
            onConfirmPrint={() => {}}
        />,
    );

    expect(markup).toContain("طباعة بوليصة مندوب المتجر");
    expect(markup).toContain('data-testid="print-store-courier-label"');
    expect(markup).toContain("تأكيد الطباعة واللصق بتصوير QR");
    expect(markup).toContain('data-testid="confirm-carrier-label-print"');
    expect(markup).not.toContain("ننتظر رابط البوليصة");
});

test("unfinished order makes the Salla completed transition explicit", () => {
    const markup = renderToStaticMarkup(
        <CarrierLabelControl
            order={{ order_number: "1002", shipping_company: "iMile" }}
            permissions={permissions}
            busy={false}
            onIssue={() => {}}
            onConfirmPrint={() => {}}
        />,
    );

    expect(markup).toContain("تحويل سلة إلى تم التنفيذ وإصدار البوليصة");
});

test("external label stays with labeling until its exact barcode is confirmed", () => {
    const pending = renderToStaticMarkup(
        <CarrierLabelControl
            order={{
                order_number: "276628330",
                shipping_company: "iMile",
                carrierSnapshot: {
                    ready: true,
                    label_url: "https://carrier.example/label.pdf",
                    tracking_number: "6081326581116",
                    courier_name: "iMile",
                    order_status_completed: true,
                },
            }}
            permissions={permissions}
            busy={false}
            onIssue={() => {}}
            onConfirmPrint={() => {}}
        />,
    );
    expect(pending).toContain("تأكيد الطباعة وتصوير باركود الشحنة");

    const confirmed = renderToStaticMarkup(
        <CarrierLabelControl
            order={{
                order_number: "276628330",
                shipping_company: "iMile",
                carrierSnapshot: {
                    ready: true,
                    label_url: "https://carrier.example/label.pdf",
                    tracking_number: "6081326581116",
                    courier_name: "iMile",
                    order_status_completed: true,
                    print_confirmed: true,
                },
            }}
            permissions={permissions}
            busy={false}
            onIssue={() => {}}
            onConfirmPrint={() => {}}
        />,
    );
    expect(confirmed).toContain("تم التنفيذ وطباعة الشحنة");
    expect(confirmed).toContain("بانتظار موظف تسليم الشحن");
});

test("shipping scan shows persistent success instead of closing silently", () => {
    const feedback = shippingScanFeedback({
        mode: "confirm_print",
        result: { already_confirmed: false },
        barcode: "6082126619113",
    });
    const markup = renderToStaticMarkup(
        <ShippingBarcodeScanner
            title="تأكيد طباعة الشحنة"
            description="اختبار"
            feedback={feedback}
            onDetected={() => {}}
            onFeedbackAction={() => {}}
            onClose={() => {}}
        />,
    );

    expect(markup).toContain('data-testid="shipping-scan-feedback-success"');
    expect(markup).toContain("تم مسح الباركود بنجاح");
    expect(markup).toContain("6082126619113");
});

test("shipping scan distinguishes a previously confirmed label", () => {
    const feedback = shippingScanFeedback({
        mode: "confirm_print",
        result: { already_confirmed: true },
        barcode: "6082126619113",
    });

    expect(feedback.kind).toBe("duplicate");
    expect(feedback.title).toBe("تم مسح البوليصة مسبقًا");
});

test("store courier confirmation explains that the label was attached", () => {
    const feedback = shippingScanFeedback({
        mode: "confirm_print",
        result: {
            already_confirmed: false,
            carrier_label_type: "store_courier",
        },
        barcode: "276628330",
    });

    expect(feedback.kind).toBe("success");
    expect(feedback.title).toBe("تم تأكيد الطباعة واللصق");
    expect(feedback.message).toContain("انتظار إسناده لمندوب التوصيل");
});

test("carrier handoff duplicate names the employee who already received it", () => {
    const feedback = shippingScanFeedback({
        mode: "carrier_handoff",
        error: {
            code: "carrier_shipment_already_received",
            details: { employee_name: "موظف تسليم الشحن" },
        },
        barcode: "6082126619113",
    });

    expect(feedback.kind).toBe("duplicate");
    expect(feedback.message).toContain("موظف تسليم الشحن");
    expect(feedback.message).toContain("لم تُضف مرة أخرى");
});


test("canonical null carrier and AWB never revive saved facts or persisted stale error", () => {
    const order = {
        order_number: "1", shipping_company: "OLD-CARRIER", carrier_tracking_number: "OLD-AWB",
        carrier_label_ready: true, carrier_label_url: "https://old.test/label",
        carrier_label_error_code: "shipping_snapshot_changed", carrier_label_error_message: "OLD-STALE",
        current_shipment: { source: "salla_current_shipping", carrier_name: null, tracking_number: null, label_available: false },
    };
    const markup = renderToStaticMarkup(<CarrierLabelControl order={order} permissions={permissions} />);
    expect(markup).not.toContain("OLD-CARRIER");
    expect(markup).not.toContain("OLD-AWB");
    expect(markup).not.toContain("OLD-STALE");
    expect(savedCarrierSnapshot(order).ready).toBe(true);
    expect(markup).not.toContain('data-testid="download-official-carrier-label"');
});

test("current carrier and AWB replace saved read facts", () => {
    const order = { carrier_name: "OLD", carrier_tracking_number: "OLD-AWB", current_shipment: {
        source: "salla_current_shipping", carrier_name: "iMile", tracking_number: "CURRENT-AWB", label_available: true,
    }};
    const snapshot = savedCarrierSnapshot(order);
    expect(snapshot.courier_name).toBe("iMile");
    expect(snapshot.tracking_number).toBe("CURRENT-AWB");
});

test("fresh guard failure remains visible instead of being suppressed as a stored error", () => {
    const markup = renderToStaticMarkup(<CarrierLabelControl permissions={permissions} order={{
        carrierSnapshot: { ready: false, error_code: "shipping_snapshot_changed", error_message: "FRESH-STALE" },
    }} />);
    expect(markup).toContain("FRESH-STALE");
});

test("opening refreshes first and opens only the new provider artifact", async () => {
    const labelWindow = { opener: "previous", location: { replace: jest.fn() } };
    const opened = jest.spyOn(window, "open").mockImplementation(() => labelWindow);
    refreshCompletedOrderCarrierLabel.mockResolvedValueOnce({ ready: true, label_url: "https://fresh.test/label" });
    await openCurrentCarrierLabel("1001");
    expect(refreshCompletedOrderCarrierLabel).toHaveBeenLastCalledWith("1001");
    expect(opened).toHaveBeenCalledWith("about:blank", "_blank");
    expect(labelWindow.location.replace).toHaveBeenCalledWith("https://fresh.test/label");
    expect(labelWindow.opener).toBeNull();
    opened.mockRestore();
});

test("stale rejection and missing artifact open nothing", async () => {
    const opened = jest.spyOn(window, "open").mockImplementation(() => null);
    refreshCompletedOrderCarrierLabel.mockRejectedValueOnce(new Error("shipping_snapshot_changed"));
    await expect(openCurrentCarrierLabel("1001")).rejects.toThrow("shipping_snapshot_changed");
    refreshCompletedOrderCarrierLabel.mockResolvedValueOnce({ ready: true });
    await expect(openCurrentCarrierLabel("1001")).rejects.toThrow();
    expect(opened).not.toHaveBeenCalled();
    opened.mockRestore();
});

test("store courier also prints only freshly verified print data", async () => {
    const popup = { opener: null, close: jest.fn() };
    const opened = jest.spyOn(window, "open").mockImplementation(() => popup);
    const data = { qr_code: "fresh-qr" };
    printStoreCourierLabel.mockReturnValueOnce(true);
    refreshCompletedOrderCarrierLabel.mockResolvedValueOnce({ ready: true, label_type: "store_courier", print_data: data });
    await openCurrentCarrierLabel("1001");
    expect(printStoreCourierLabel).toHaveBeenLastCalledWith(popup, data);
    opened.mockRestore();
});

test("cached download remains disabled without print permission", () => {
    const markup = renderToStaticMarkup(<CarrierLabelControl permissions={{ can_print: false }} order={{
        carrierSnapshot: { ready: true, label_url: "https://saved.test/label" },
    }} />);
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*data-testid="download-official-carrier-label"/);
    expect(markup).not.toContain("https://saved.test/label");
});


test("fresh issue response takes precedence over the earlier list projection", () => {
    const markup = renderToStaticMarkup(<CarrierLabelControl permissions={permissions} order={{
        current_shipment: { source: "salla_current_shipping", label_available: false, carrier_name: null, tracking_number: null },
        carrierSnapshot: { verified_action: true, ready: true, label_url: "https://new.test/label", courier_name: "iMile", tracking_number: "NEW-AWB" },
    }} />);
    expect(markup).toContain('data-testid="download-official-carrier-label"');
    expect(markup).toContain("NEW-AWB");
});


test("full completed orders page loads a canonical order and renders its card", async () => {
    listCompletedFulfillmentOrders.mockResolvedValueOnce({ items: [{
        order_number: "TEST-ORDER-42", items: [], current_shipment: {
            source: "salla_current_shipping", carrier_name: "iMile", tracking_number: "CURRENT-42",
            label_available: false, label_status: "none",
        },
    }], permissions });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    global.IS_REACT_ACT_ENVIRONMENT = true;
    try {
        await act(async () => { root.render(<CompletedFulfillmentOrders />); });
        expect(container.textContent).toContain("#TEST-ORDER-42");
        expect(container.textContent).toContain("لا توجد بوليصة للشحنة الحالية");
        expect(container.querySelector('[data-testid="current-shipment-facts"]').textContent).toContain("CURRENT-42");
    } finally {
        act(() => root.unmount());
        container.remove();
        delete global.IS_REACT_ACT_ENVIRONMENT;
    }
});

test("without canonical projection saved stale error remains visible", () => {
    const order = { carrier_label_error_code: "shipping_snapshot_changed", carrier_label_error_message: "SAVED-STALE" };
    expect(savedCarrierSnapshot(order).error_message).toBe("SAVED-STALE");
});

test("projection availability does not authorize a saved ready label", () => {
    const order = { carrier_label_ready: false, current_shipment: {
        source: "salla_current_shipping", label_available: true, label_status: "available",
    }};
    expect(savedCarrierSnapshot(order).ready).toBe(false);
    const markup = renderToStaticMarkup(<CarrierLabelControl order={order} permissions={permissions} />);
    expect(markup).not.toContain('data-testid="download-official-carrier-label"');
    expect(markup).toContain("يلزم التحقق قبل فتحها");
});

test("cancelled canonical shipment explicitly displays cancelled status", () => {
    const markup = renderToStaticMarkup(<CarrierLabelControl permissions={permissions} order={{ current_shipment: {
        source: "salla_current_shipping", label_available: false, label_status: "cancelled",
    }}} />);
    expect(markup).toContain("الشحنة الحالية ملغاة");
});


test.each([false, true])("current shipment button refreshes without issuance or retry (failure=%s)", async (fails) => {
    listCompletedFulfillmentOrders.mockResolvedValueOnce({ items: [{
        order_number: "CURRENT-ORDER", carrier_label_ready: false, items: [], current_shipment: {
            source: "salla_current_shipping", shipment_id: "CURRENT-ID", tracking_number: "CURRENT-AWB",
            label_available: true, label_status: "available",
        },
    }], permissions });
    if (fails) refreshCompletedOrderCarrierLabel.mockRejectedValueOnce(new Error("FRESH-STALE"));
    else refreshCompletedOrderCarrierLabel.mockResolvedValueOnce({ ready: true, label_url: "https://fresh.test/label" });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    global.IS_REACT_ACT_ENVIRONMENT = true;
    try {
        await act(async () => { root.render(<CompletedFulfillmentOrders />); });
        const button = container.querySelector('[data-testid="issue-official-carrier-label"]');
        expect(button.textContent).toContain("التحقق من البوليصة الحالية");
        await act(async () => { button.click(); });
        expect(refreshCompletedOrderCarrierLabel).toHaveBeenCalledTimes(1);
        expect(refreshCompletedOrderCarrierLabel).toHaveBeenCalledWith("CURRENT-ORDER");
        expect(issueCompletedOrderCarrierLabel).not.toHaveBeenCalled();
        if (fails) expect(container.textContent).toContain("FRESH-STALE");
    } finally {
        act(() => root.unmount());
        container.remove();
        delete global.IS_REACT_ACT_ENVIRONMENT;
    }
});


test("blocked external popup reports action error after fresh verification", async () => {
    const opened = jest.spyOn(window, "open").mockReturnValue(null);
    refreshCompletedOrderCarrierLabel.mockResolvedValueOnce({ ready: true, label_url: "https://fresh.test/label" });
    await expect(openCurrentCarrierLabel("1001")).rejects.toThrow("اسمح بالنوافذ المنبثقة");
    expect(refreshCompletedOrderCarrierLabel).toHaveBeenCalledTimes(1);
    expect(opened).toHaveBeenCalledTimes(1);
    opened.mockRestore();
});
