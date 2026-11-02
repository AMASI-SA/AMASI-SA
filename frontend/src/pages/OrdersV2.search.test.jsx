import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OrdersV2 from "./OrdersV2";
import { listOrders, getOrderFilterSummary } from "../services/orderEngine";

jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn(), useSearchParams: () => [new URLSearchParams()] }));
jest.mock("../services/orderEngine", () => ({ listOrders: jest.fn(), getOrder: jest.fn(), getOrderFilterSummary: jest.fn(), ORDER_PAGE_SIZE: 15 }));

let root, host;
const click = async (element) => act(async () => element.click());
const button = (text) => [...host.querySelectorAll("button")].find((node) => node.textContent === text);
const advance = async (ms) => act(async () => jest.advanceTimersByTime(ms));

beforeEach(async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-10-17T12:00:00Z"));
    jest.clearAllMocks();
    listOrders.mockResolvedValue({ items: [], nextCursor: null });
    getOrderFilterSummary.mockResolvedValue({ total: 0, statusCards: [] });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => root.render(<OrdersV2 />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); });

test("campaign and committed date range search automatically, draft selection does not", async () => {
    const input = host.querySelector('input[placeholder="Campaign ID"]');
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "campaign-123");
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await advance(500);
    expect(listOrders).toHaveBeenCalledTimes(2);
    await click(host.querySelector('[data-testid="ads-arabic-date-range-picker"] > button'));
    await click(button("من بداية الشهر"));
    await advance(600);
    expect(listOrders).toHaveBeenCalledTimes(2);
    await click(button("حفظ وتطبيق"));
    await advance(500);
    expect(listOrders).toHaveBeenCalledTimes(3);
    expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({ filters: expect.objectContaining({
        campaign_id: "campaign-123", created_from: "2026-10-01T00:00:00+03:00", created_to: "2026-10-18T00:00:00+03:00",
    }) }));
    expect(host.querySelector('input[type="datetime-local"]')).toBeNull();
    await click(button("مسح الفترة"));
    await advance(500);
    expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({ filters: expect.objectContaining({ campaign_id: "campaign-123", created_from: "", created_to: "" }) }));
    await click(button(" مسح") || [...host.querySelectorAll("button")].find((node) => node.textContent.trim() === "مسح"));
    expect(input.value).toBe("");
    expect(listOrders).toHaveBeenLastCalledWith({ limit: 15, statusExact: null, statusGroup: null });
});
