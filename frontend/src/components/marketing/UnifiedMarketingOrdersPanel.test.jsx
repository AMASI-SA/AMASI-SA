import React, { act } from "react";
import { createRoot } from "react-dom/client";

import UnifiedMarketingOrdersPanel from "./UnifiedMarketingOrdersPanel";

describe("UnifiedMarketingOrdersPanel", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("renders provider-neutral commerce money and comparison totals", async () => {
        await act(async () => {
            root.render(
                <UnifiedMarketingOrdersPanel
                    report={{
                        order_summary: {
                            matched_financial_orders: 3,
                            matched_financial_revenue: { amount: 150, currency: "SAR" },
                            platform_attributed_conversions: 2,
                            unmatched_orders: 1,
                        },
                        orders: [{
                            order_number: "1001",
                            local_date: "2026-08-22",
                            campaign_id: "c1",
                            campaign_name: "Campaign",
                            amount: { amount: 50, currency: "SAR" },
                            status: "completed",
                            match_method: "campaign_id",
                        }],
                    }}
                />,
            );
        });

        expect(container.textContent).toContain("150.00 SAR");
        expect(container.textContent).toContain("50.00 SAR");
        expect(container.textContent).toContain("1001");
    });
});
