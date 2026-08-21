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

import {
    CarrierLabelControl,
    shippingScanFeedback,
} from "./CompletedFulfillmentOrders";
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
    expect(markup).toContain("https://carrier.example/label.pdf");
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
