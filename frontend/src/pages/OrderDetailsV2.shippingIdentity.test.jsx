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

test("a carrier switch removes the prior verified label and message", async () => {
    issueShippingLabel.mockResolvedValue(storeResult);
    await render();
    await clickLabel();
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
    await render({ company: "مندوب الرياض", company_code: "store" });
    expect(host.textContent).toContain("إصدار البوليصة");
    expect(host.textContent).not.toContain("بوليصة جديدة مؤكدة");
    expect(host.textContent).not.toContain("WAY-NEW");
});

test("a same-carrier reload keeps the just-issued legitimate label", async () => {
    issueShippingLabel.mockResolvedValue(storeResult);
    await render();
    await clickLabel();
    await render({ ...carrier, tracking_number: "WAY-NEW", status: "created" });
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
    expect(host.textContent).toContain("بوليصة جديدة مؤكدة");
    await clickLabel();
    expect(printStoreCourierLabel).toHaveBeenCalledTimes(2);
});

test("filling the same carrier code does not discard its freshly issued label", async () => {
    issueShippingLabel.mockResolvedValue(storeResult);
    await render({ company: carrier.company });
    await clickLabel();
    await render({ ...carrier, tracking_number: "WAY-NEW" });
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
    expect(host.textContent).toContain("بوليصة جديدة مؤكدة");
});

test.each(["carrier", "order"])("a late issue response cannot print after a %s switch", async (change) => {
    const pending = deferred();
    issueShippingLabel.mockReturnValue(pending.promise);
    await render();
    await clickLabel();
    await render(change === "carrier" ? { company: "مندوب الرياض", company_code: "store" } : carrier,
        change === "order" ? "B" : "A");
    await act(async () => { pending.resolve(storeResult); });
    expect(printStoreCourierLabel).not.toHaveBeenCalled();
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
    expect(host.textContent).not.toContain("WAY-NEW");
    expect(onIssued).not.toHaveBeenCalled();
});

test("a late verification cannot print the previous carrier label", async () => {
    const pending = deferred();
    verifyShippingLabel.mockReturnValue(pending.promise);
    await render({ ...carrier, tracking_number: "OLD-WAY", label_url: "https://example.test/old.pdf", status: "created" });
    await clickLabel();
    await render({ company: "مندوب الرياض", company_code: "store" });
    await act(async () => { pending.resolve({ ready: true, tracking_number: "OLD-WAY", label_url: "https://example.test/old.pdf" }); });
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
    expect(onIssued).not.toHaveBeenCalled();
});

test("switching away and back never revives the old carrier's pending print", async () => {
    const pending = deferred();
    issueShippingLabel.mockReturnValue(pending.promise);
    await render();
    await clickLabel();
    await render({ company: "مندوب الرياض", company_code: "store" });
    await render();
    await act(async () => { pending.resolve(storeResult); });
    expect(printStoreCourierLabel).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain("WAY-NEW");
});

test.each(["cancelled", "canceled", "void", "deleted"])("canonical %s invalidates a verified cached label", async (status) => {
    issueShippingLabel.mockResolvedValue(storeResult);
    await render();
    await clickLabel();
    await render({ ...carrier, status });
    expect(host.textContent).not.toContain("طباعة بوليصة المتجر");
    expect(host.textContent).not.toContain("WAY-NEW");
    expect(host.textContent).not.toContain("بوليصة جديدة مؤكدة");
});

test("cancellation while issuing prevents the late response from printing", async () => {
    const pending = deferred();
    issueShippingLabel.mockReturnValue(pending.promise);
    await render({ ...carrier, status: "pending" });
    await clickLabel();
    await render({ ...carrier, status: "cancelled" });
    await act(async () => { pending.resolve(storeResult); });
    expect(printStoreCourierLabel).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
});

test("a different same-carrier tracking invalidates the previous cached label", async () => {
    issueShippingLabel.mockResolvedValue(storeResult);
    await render();
    await clickLabel();
    await render({ ...carrier, tracking_number: "WAY-NEW", status: "created" });
    await render({ ...carrier, tracking_number: "WAY-REPLACEMENT", status: "created" });
    expect(host.textContent).not.toContain("طباعة بوليصة المتجر");
    expect(host.textContent).toContain("WAY-REPLACEMENT");
    expect(host.textContent).not.toContain("بوليصة جديدة مؤكدة");
});

test("a fresh same-carrier shipment can arrive before its issuance response", async () => {
    const pending = deferred();
    issueShippingLabel.mockReturnValue(pending.promise);
    await render({ ...carrier, tracking_number: "OLD", status: "pending" });
    await clickLabel();
    await render({ ...carrier, tracking_number: "WAY-NEW", status: "created" });
    await act(async () => { pending.resolve(storeResult); });
    expect(printStoreCourierLabel).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
});

test("a just-issued label survives the pending baseline until the fresh local shipment arrives", async () => {
    issueShippingLabel.mockResolvedValue(storeResult);
    await render({ ...carrier, tracking_number: "OLD", status: "pending" });
    await clickLabel();
    await render({ ...carrier, tracking_number: "OLD", status: "pending" });
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
    expect(host.textContent).toContain("WAY-NEW");
    await render({ ...carrier, tracking_number: "WAY-NEW", status: "created" });
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
});

test("a replaced tracking rejects a late response for the old shipment", async () => {
    const pending = deferred();
    verifyShippingLabel.mockReturnValue(pending.promise);
    await render({ ...carrier, tracking_number: "OLD", label_url: "https://example.test/old.pdf", status: "created" });
    await clickLabel();
    await render({ ...carrier, tracking_number: "NEW", label_url: "https://example.test/new.pdf", status: "created" });
    await act(async () => { pending.resolve({ ready: true, tracking_number: "OLD", label_url: "https://example.test/old.pdf" }); });
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
});

test("a same-carrier shipment replacement invalidates the cached label before its AWB arrives", async () => {
    issueShippingLabel.mockResolvedValue({ ...storeResult, shipment_id: "OLD-ID" });
    await render({ ...carrier, shipment_id: "OLD-ID" });
    await clickLabel();
    await render({ ...carrier, shipment_id: "NEW-ID", tracking_number: null, status: "pending" });
    expect(host.textContent).not.toContain("طباعة بوليصة المتجر");
    expect(host.textContent).not.toContain("WAY-NEW");
    expect(host.textContent).not.toContain("بوليصة جديدة مؤكدة");
});

test("a late issuance for a replaced shipment cannot print even before the new AWB arrives", async () => {
    const pending = deferred();
    issueShippingLabel.mockReturnValue(pending.promise);
    await render({ ...carrier, shipment_id: "OLD-ID", status: "pending" });
    await clickLabel();
    await render({ ...carrier, shipment_id: "NEW-ID", tracking_number: null, status: "pending" });
    await act(async () => { pending.resolve({ ...storeResult, shipment_id: "OLD-ID" }); });
    expect(printStoreCourierLabel).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
    expect(host.textContent).not.toContain("WAY-NEW");
});

test("the freshly issued shipment survives its old local ID until the matching webhook arrives", async () => {
    issueShippingLabel.mockResolvedValue({ ...storeResult, shipment_id: "NEW-ID" });
    await render({ ...carrier, shipment_id: "OLD-ID", status: "pending" });
    await clickLabel();
    await render({ ...carrier, shipment_id: "OLD-ID", status: "pending" });
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
    await render({ ...carrier, shipment_id: "NEW-ID", tracking_number: "WAY-NEW", status: "created" });
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
    expect(host.textContent).toContain("بوليصة جديدة مؤكدة");
});

test("a matching fresh shipment ID can arrive before its issuance response", async () => {
    const pending = deferred();
    issueShippingLabel.mockReturnValue(pending.promise);
    await render({ ...carrier, shipment_id: "OLD-ID", status: "pending" });
    await clickLabel();
    await render({ ...carrier, shipment_id: "NEW-ID", tracking_number: null, status: "pending" });
    await act(async () => { pending.resolve({ ...storeResult, shipment_id: "NEW-ID" }); });
    expect(printStoreCourierLabel).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("طباعة بوليصة المتجر");
});

test("a late verification for an old shipment ID cannot print while its replacement has no AWB", async () => {
    const pending = deferred();
    verifyShippingLabel.mockReturnValue(pending.promise);
    await render({ ...carrier, shipment_id: "OLD-ID", tracking_number: "OLD", label_url: "https://example.test/old.pdf", status: "created" });
    await clickLabel();
    await render({ ...carrier, shipment_id: "NEW-ID", tracking_number: null, status: "pending" });
    await act(async () => { pending.resolve({ ready: true, shipment_id: "OLD-ID", tracking_number: "OLD", label_url: "https://example.test/old.pdf" }); });
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalled();
});
