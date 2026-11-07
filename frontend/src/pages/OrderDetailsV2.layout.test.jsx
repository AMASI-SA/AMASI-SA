import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OrderDetailsV2 from "./OrderDetailsV2.jsx";

jest.mock("react-router-dom", () => ({
    Link: ({ children }) => <a>{children}</a>,
    useParams: () => ({ orderNumber: "123" }),
    useSearchParams: () => [new URLSearchParams()],
}));
jest.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: { id: "owner" } }) }));
jest.mock("../components/PermissionRoute", () => ({ userHasPermission: () => true }));
jest.mock("../hooks/useOrders", () => ({ useOrder: () => ({ order: {
    order_number: "123", totals: { total: 100, subtotal: 100 }, customer: {},
}, loading: false }) }));
jest.mock("../hooks/useOrderItems", () => ({ useOrderItems: () => ({ items: [], loading: false }) }));
jest.mock("../hooks/useSourceAdditionReview", () => ({ useSourceAdditionReview: () => null }));
jest.mock("../components/orders/SourceAdditionReview", () => ({
    SourceAdditionReviewNotice: () => null, SourceAdditionReviewButton: () => null,
}));
jest.mock("../services/orderEngine", () => ({ markOrderRead: jest.fn(() => Promise.resolve()) }));
jest.mock("../components/orders/ReturnDecisionCard", () => () => null);
jest.mock("../components/orders/OrderActivityPanel", () => () => null);
jest.mock("../components/orders/BundleOperationalPanel", () => () => <section data-testid="real-bundle-workflow" />);
jest.mock("./OrderTrackingNotes", () => ({ OrderTrackingNotesPanel: () => <section data-testid="real-order-tracking" /> }));

test("summary immediately follows items while operational panels remain and replay is absent", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement("div");
    const root = createRoot(host);
    try {
        await act(async () => { root.render(<OrderDetailsV2 />); });
        expect(host.querySelector('[data-testid="order-v2-items"]').nextElementSibling)
            .toBe(host.querySelector('[data-testid="order-v2-summary"]'));
        expect(host.querySelector('[data-testid="real-bundle-workflow"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="real-order-tracking"]')).not.toBeNull();
        expect(host.textContent).not.toContain("تجربة مراحل التجهيز والإيقاف");
        expect(host.textContent).not.toContain("كتجربة جديدة");
    } finally {
        await act(async () => root.unmount());
    }
});
