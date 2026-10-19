import { act } from "react";
import { createRoot } from "react-dom/client";
import { ReviewDrawer } from "./OrderReview";
import { getOrderReview, completeOrderReview } from "../services/orderReviewEngine";

jest.mock("../services/orderReviewEngine", () => ({
    getOrderReview: jest.fn(), completeOrderReview: jest.fn(),
}));
jest.mock("../reviewConfirmation", () => ({
    watchReviewConfirmation: () => () => {},
}));
jest.mock("../components/fulfillment/CustomerServiceInstructionBanner", () => () => null);

test.each([true, false, undefined])("gift address blocked=%s controls review action", async (blocked) => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    completeOrderReview.mockClear();
    getOrderReview.mockResolvedValue({
        order: { order_number: "synthetic", created_at: "2026-10-19", totals: {} },
        items: [], revision: 0,
        gift_address_review: blocked === undefined ? undefined : {
            blocked, message: blocked ? "عنوان مستلم الهدية غير مكتمل" : null,
        },
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
        await act(async () => root.render(<ReviewDrawer orderNumber="synthetic" onClose={() => {}} onCompleted={() => {}} />));
        const button = [...host.querySelectorAll("button")].find(node => node.textContent.trim() === "تمت المراجعة");
        expect(button).toBeTruthy();
        expect(button.disabled).toBe(blocked === true);
        if (blocked) {
            expect(host.querySelector('[role="alert"]').textContent).toContain("عنوان مستلم الهدية");
            await act(async () => button.click());
            expect(completeOrderReview).not.toHaveBeenCalled();
        }
    } finally {
        await act(async () => root.unmount());
        host.remove();
    }
});
