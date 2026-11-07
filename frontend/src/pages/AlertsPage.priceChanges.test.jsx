import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import AlertsPage from "./AlertsPage";

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({ useNavigate: () => mockNavigate }));
jest.mock("../lib/api", () => ({
    __esModule: true, default: { get: jest.fn(), post: jest.fn() }, formatApiErrorDetail: () => "",
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const priceAlert = {
    id: "price-alert", alert_type: "employee_price_change", severity: "info", status: "new",
    title: "طلب تغيير سعر الشال", message: "خالد يقترح 18 ريالًا", created_at: "2026-11-08T09:00:00+00:00",
    related_entity_url: "/price-change-requests/request-1",
};
let node;
let root;
beforeEach(() => {
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    api.get.mockResolvedValue({ data: { alerts: [priceAlert, { ...priceAlert, id: "settlement-alert", title: "فرق تسوية", alert_type: "amount_diff", related_entity_url: "/settlements" }], by_severity: { info: 2 }, total: 2 } });
    api.post.mockResolvedValue({ data: { ok: true } });
});
afterEach(() => { act(() => root.unmount()); node.remove(); });

test("price requests can be filtered and their titles open a review without changing the notification", async () => {
    await act(async () => root.render(<AlertsPage />));
    const type = node.querySelector('[data-testid="alerts-filter-type"]');
    expect([...type.options].some(option => option.value === "employee_price_change" && option.textContent === "طلب تغيير سعر")).toBe(true);
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(type, "employee_price_change");
        type.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(node.querySelector('[data-testid="alerts-page-row-settlement-alert"]')).toBeNull();
    const title = [...node.querySelectorAll("button")].find(button => button.textContent === priceAlert.title);
    await act(async () => title.click());
    expect(mockNavigate).toHaveBeenCalledWith(priceAlert.related_entity_url);
    expect(api.post).not.toHaveBeenCalled();
});

test("explicit mark-read remains a notification action and other alerts keep their existing click-through", async () => {
    await act(async () => root.render(<AlertsPage />));
    await act(async () => node.querySelector('[data-testid="alerts-page-read-price-alert"]').click());
    expect(api.post).toHaveBeenCalledWith("/alerts/price-alert/read", {});
    await act(async () => node.querySelector('[data-testid="alerts-page-goto-settlement-alert"]').click());
    expect(api.post).toHaveBeenCalledWith("/alerts/settlement-alert/read", {});
    expect(mockNavigate).toHaveBeenCalledWith("/settlements");
    expect(api.post.mock.calls.every(call => call[0].startsWith("/alerts/"))).toBe(true);
});
