import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import NotificationBell from "./NotificationBell";

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({ useNavigate: () => mockNavigate }));
jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { get: jest.fn(), post: jest.fn() },
    formatApiErrorDetail: () => "",
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const priceAlert = {
    id: "alert-price-1",
    alert_type: "employee_price_change",
    severity: "info",
    status: "new",
    title: "طلب تغيير سعر الشال",
    message: "خالد يقترح تغيير السعر إلى 18 ريالًا",
    related_entity_url: "/price-change-requests/request-1",
};
let container;
let root;

beforeEach(() => {
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.get.mockImplementation(async url => ({ data: url === "/alerts/unread-count"
        ? { count: 1 }
        : { alerts: [priceAlert], by_severity: { critical: 0, warning: 0, info: 1 } } }));
    api.post.mockResolvedValue({ data: { ok: true } });
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
});

async function openBell() {
    await act(async () => root.render(<NotificationBell />));
    await act(async () => container.querySelector('[data-testid="notification-bell-btn"]').click());
}

test("the price notification title opens its review without deciding or hiding the pending request", async () => {
    await openBell();
    const title = [...container.querySelectorAll("button")].find(button => button.textContent === priceAlert.title);
    expect(title).toBeDefined();
    await act(async () => title.click());
    expect(mockNavigate).toHaveBeenCalledWith(priceAlert.related_entity_url);
    expect(api.post).not.toHaveBeenCalled();
});

test("the price action names the review and leaves other alert navigation unchanged", async () => {
    await openBell();
    const open = container.querySelector('[data-testid="notification-goto-alert-price-1"]');
    expect(open.textContent).toContain("مراجعة تغيير السعر");
    await act(async () => open.click());
    expect(api.post).not.toHaveBeenCalled();
    api.get.mockImplementation(async url => ({ data: url === "/alerts/unread-count"
        ? { count: 1 }
        : { alerts: [{ ...priceAlert, alert_type: "amount_diff", related_entity_url: "/settlements" }], by_severity: { critical: 0, warning: 0, info: 1 } } }));
    await act(async () => container.querySelector('[data-testid="notification-bell-btn"]').click());
    await act(async () => container.querySelector('[data-testid="notification-goto-alert-price-1"]').click());
    expect(api.post).toHaveBeenCalledWith("/alerts/alert-price-1/read");
    expect(mockNavigate).toHaveBeenLastCalledWith("/settlements");
});

test("a confirmed price decision refreshes the existing unread badge", async () => {
    await act(async () => root.render(<NotificationBell />));
    api.get.mockResolvedValue({ data: { count: 0 } });
    await act(async () => window.dispatchEvent(new Event("mezan:price-change-decided")));
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="notification-bell-count"]')).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
});
