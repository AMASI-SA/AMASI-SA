import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OrdersV2 from "./OrdersV2";

global.IS_REACT_ACT_ENVIRONMENT = true;
const mockSearch = jest.fn();
jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
    useSearchParams: () => [new URLSearchParams("sku=AMS13217&created_from=2026-11-10T00%3A00%3A00%2B03%3A00&created_to=2026-11-11T00%3A00%3A00%2B03%3A00")],
}));
jest.mock("../context/AuthContext", () => ({ useOptionalAuth: () => ({ user: null }) }));
jest.mock("../services/orderEngine", () => ({ getOrderFilterSummary: async () => ({ total: 0, statusCards: [], statusCounts: {} }) }));
jest.mock("../hooks/useOrders", () => ({ useOrders: () => ({
    orders: [{ order_number: "123", source: { source: "snapchat", campaign_name: "حملة الشتاء" }, customer: {}, shipping: {}, payment: {}, totals: {}, items: [] }],
    hasMore: false, searchMode: true, searchCombined: mockSearch,
}) }));

test("opens the supplied product period and shows the existing campaign name on hover", async () => {
    const host = document.createElement("div");
    const root = createRoot(host);
    await act(async () => root.render(<OrdersV2 />));
    expect(mockSearch).toHaveBeenCalledWith(expect.objectContaining({ sku: "AMS13217", created_from: "2026-11-10T00:00:00+03:00", created_to: "2026-11-11T00:00:00+03:00" }));
    expect(host.querySelector('[title="حملة الشتاء"]')).not.toBeNull();
    expect(host.querySelector('input[type="datetime-local"]').value).toBe("2026-11-10T00:00");
    await act(async () => root.unmount());
});
