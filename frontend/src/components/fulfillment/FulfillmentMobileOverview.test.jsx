import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../../services/orderReviewEngine", () => ({
    listPendingOrderReviews: jest.fn(),
    listPreparationFiles: jest.fn(),
    listReviewedProductCatalog: jest.fn(),
}));
jest.mock("../../services/preparationWorkService", () => ({
    getMyPreparationWork: jest.fn(),
}));
jest.mock("../../services/fulfillmentV2", () => ({
    listReadyToShipOrders: jest.fn(),
}));

import FulfillmentMobileOverview from "./FulfillmentMobileOverview";
import {
    listPendingOrderReviews,
    listPreparationFiles,
    listReviewedProductCatalog,
} from "../../services/orderReviewEngine";
import { getMyPreparationWork } from "../../services/preparationWorkService";
import { listReadyToShipOrders } from "../../services/fulfillmentV2";

test("mobile overview loads real summaries and opens the approved quick actions", async () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = jest.fn(() => ({
        matches: true,
        addEventListener: jest.fn(),
        removeEventListener: jest.fn(),
    }));
    listPendingOrderReviews.mockResolvedValue({ items: [{}, {}], nextCursor: "next" });
    listReviewedProductCatalog.mockResolvedValue({ summary: { reviewed_order_count: 7 } });
    getMyPreparationWork.mockResolvedValue({ summary: { in_progress: 42 } });
    listReadyToShipOrders.mockResolvedValue({ total: 12, items: [] });
    listPreparationFiles.mockResolvedValue({
        items: [{
            file_number: "PF-20260806-0001",
            file_title: "ملف اليوم الوطني",
            allocated_quantity: 24,
            responsible_employee_name: "محمد",
        }],
    });
    const onOpenStage = jest.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;

    try {
        await act(async () => {
            root.render(
                <FulfillmentMobileOverview
                    onOpenStage={onOpenStage}
                    stages={[
                        { key: "pending_review", shortLabel: "بانتظار المراجعة", Icon: () => <span /> },
                        { key: "delivered", shortLabel: "تم التوصيل", Icon: () => <span /> },
                    ]}
                />,
            );
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        });

        expect(container.textContent).toContain("2+");
        expect(container.textContent).toContain("42");
        expect(container.textContent).toContain("12");
        expect(container.textContent).toContain("ملف اليوم الوطني");
        const supplierAction = Array.from(container.querySelectorAll("button")).find(
            (button) => button.textContent.includes("استلام المورد"),
        );
        act(() => supplierAction.dispatchEvent(new MouseEvent("click", { bubbles: true })));
        expect(onOpenStage).toHaveBeenCalledWith("preparation");

        const allStages = Array.from(container.querySelectorAll("button")).find(
            (button) => button.textContent.includes("عرض جميع المراحل"),
        );
        act(() => allStages.dispatchEvent(new MouseEvent("click", { bubbles: true })));
        expect(container.querySelector('[data-testid="fulfillment-mobile-all-stages"]')?.textContent).toContain("تم التوصيل");
    } finally {
        await act(async () => root.unmount());
        container.remove();
        window.matchMedia = originalMatchMedia;
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
