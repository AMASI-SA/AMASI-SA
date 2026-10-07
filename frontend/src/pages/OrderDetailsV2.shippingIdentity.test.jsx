import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { ShippingCard } from "./OrderDetailsV2.jsx";
import { issueShippingLabel, verifyShippingLabel } from "../services/orderEngine";
import { printStoreCourierLabel } from "../lib/storeCourierLabelPrint";

jest.mock("react-router-dom", () => ({}), { virtual: true });
jest.mock("@phosphor-icons/react", () => ({
    Truck: () => null, Printer: () => null, SpinnerGap: () => null,
    Copy: () => null, CheckCircle: () => null, MapPin: () => null,
}));
jest.mock("../hooks/useOrders", () => ({}));
jest.mock("../hooks/useOrderItems", () => ({}));
jest.mock("../components/orders/ReturnDecisionCard", () => () => null);
jest.mock("../components/orders/OrderActivityPanel", () => () => null);
jest.mock("../components/fulfillment/FulfillmentExperimentPanel", () => () => null);
jest.mock("../services/orderEngine", () => ({ issueShippingLabel: jest.fn(), verifyShippingLabel: jest.fn() }));
jest.mock("../lib/storeCourierLabelPrint", () => ({ printStoreCourierLabel: jest.fn(() => true) }));

let host;
let root;
let popup;
let onIssued;
const carrier = { company: "iMile للتوصيل", company_code: "imile" };
const storeResult = {
    ready: true, label_type: "store_courier", status: "store_courier",
    tracking_number: "WAY-NEW", message: "بوليصة جديدة مؤكدة",
    print_data: { qr_code: "data:image/png;base64,QR" },
};
function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}
async function render(shipping = carrier, orderNumber = "A") {
    expect(typeof ShippingCard).toBe("function");
    await act(async () => {
        root.render(<ShippingCard shipping={shipping} customer={{}} orderNumber={orderNumber} allowPrinting onIssued={onIssued} />);
    });
}
async function clickLabel() {
    await act(async () => { host.querySelector("button").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
}
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    // react-scripts resets mock implementations before each test.
    printStoreCourierLabel.mockReturnValue(true);
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    popup = { document: {}, location: { replace: jest.fn() }, close: jest.fn() };
    jest.spyOn(window, "open").mockReturnValue(popup);
    onIssued = jest.fn();
});
afterEach(async () => {
    await act(async () => { root.unmount(); });
    host.remove();
    jest.restoreAllMocks();
});

test("legacy print accepts simultaneous shipment ID, AWB, carrier and clock differences", async () => {
    const pending = deferred();
    verifyShippingLabel.mockReturnValueOnce(pending.promise);
    await render({ ...carrier, shipment_id: "LOCAL", tracking_number: "LOCAL-AWB", label_url: "https://old.test/label" });
    await clickLabel();
    await render({ company: "SMSA", company_code: "smsa", shipment_id: "OTHER",
        tracking_number: "OTHER-AWB", carrier_updated_at: "2099-01-01", label_url: "https://other.test/label" });
    await act(async () => pending.resolve({ ready: true, shipment_id: "SALLA", tracking_number: "SALLA-AWB", label_url: "https://salla.test/ready.pdf" }));
    expect(popup.location.replace).toHaveBeenCalledWith("https://salla.test/ready.pdf");
    expect(issueShippingLabel).not.toHaveBeenCalled();
    expect(onIssued).not.toHaveBeenCalled();
});

test("legacy store-courier printing always uses the read endpoint and formatter", async () => {
    verifyShippingLabel.mockResolvedValue(storeResult);
    await render({ company: "مندوب المتجر", shipment_id: "OLD", tracking_number: "OLD", carrier_updated_at: "2099" });
    await clickLabel();
    await clickLabel();
    expect(verifyShippingLabel).toHaveBeenCalledTimes(2);
    expect(printStoreCourierLabel).toHaveBeenCalledTimes(2);
    expect(printStoreCourierLabel).toHaveBeenLastCalledWith(popup, storeResult.print_data);
    expect(issueShippingLabel).not.toHaveBeenCalled();
});

test.each(["order", "unmount", "permission"])("late print cannot open after %s changes", async (change) => {
    const pending = deferred();
    verifyShippingLabel.mockReturnValueOnce(pending.promise);
    await render();
    await clickLabel();
    if (change === "order") await render(carrier, "B");
    if (change === "unmount") await act(async () => root.render(null));
    if (change === "permission") await act(async () => root.render(<ShippingCard shipping={carrier} customer={{}} orderNumber="A" allowPrinting={false} />));
    await act(async () => pending.resolve({ ready: true, label_url: "https://salla.test/ready.pdf" }));
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(printStoreCourierLabel).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
});

test.each(["pending", "failure"])("%s closes the window without printing a cached label or issuing", async (kind) => {
    if (kind === "failure") verifyShippingLabel.mockRejectedValueOnce(new Error("تعذّر التحقق من البوليصة الحالية في سلة."));
    else verifyShippingLabel.mockResolvedValueOnce({ ready: false, message: "لا توجد بوليصة فعّالة حاليًا في سلة؛ أوقفت الطباعة." });
    await render({ ...carrier, tracking_number: "OLD", label_url: "https://old.test/label" });
    await clickLabel();
    expect(popup.close).toHaveBeenCalled();
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(issueShippingLabel).not.toHaveBeenCalled();
    expect(host.textContent).toContain(kind === "failure" ? "تعذّر التحقق" : "لا توجد بوليصة فعّالة");
});

test("printing remains unavailable without permission", async () => {
    await act(async () => root.render(<ShippingCard shipping={carrier} customer={{}} orderNumber="A" allowPrinting={false} />));
    expect(host.textContent).toContain("الطباعة بعد اكتمال التجهيز");
    expect(verifyShippingLabel).not.toHaveBeenCalled();
});

test("rapid repeated clicks start one read", async () => {
    const pending = deferred();
    verifyShippingLabel.mockReturnValueOnce(pending.promise);
    await render();
    await act(async () => {
        host.querySelector("button").click();
        host.querySelector("button").click();
    });
    expect(verifyShippingLabel).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve({ ready: false }));
});
