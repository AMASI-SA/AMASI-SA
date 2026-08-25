import React, { act } from "react";
import { createRoot } from "react-dom/client";

import SnapchatOrderSourceAudit from "./SnapchatOrderSourceAudit";
import { getSnapchatOrderSourceAudit } from "../../services/snapchatOrderSourceAudit";

jest.mock("../../services/snapchatOrderSourceAudit", () => ({
    getSnapchatOrderSourceAudit: jest.fn(),
}));

describe("SnapchatOrderSourceAudit", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        getSnapchatOrderSourceAudit.mockReset();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("shows Salla truth, non-campaign orders and platform attribution separately", async () => {
        getSnapchatOrderSourceAudit.mockResolvedValue({
            account: { account_name: "حساب الرياض" },
            summary: {
                campaign_matched_orders: 42,
                snapchat_attribution_gap_orders: 10,
                non_campaign_orders: 10,
                total_salla_created_orders: 52,
                total_financial_sales_sar: 12345.67,
                platform_attributed_purchases: 48,
                date_timezone: "Asia/Riyadh",
            },
            orders_total: 2,
            orders: [
                {
                    order_number: "1001",
                    local_created_at: "2026-08-05T13:00:00+03:00",
                    amount_sar: 250,
                    classification: "matched",
                    campaign_name: "حملة الرياض",
                    campaign_id: "campaign-1",
                    match_method: "campaign_id",
                    source_label: "snapchat",
                    financially_included: true,
                    status: "completed",
                },
                {
                    order_number: "1002",
                    local_created_at: "2026-08-05T14:00:00+03:00",
                    amount_sar: 150,
                    classification: "non_campaign",
                    match_method: "unmatched",
                    source_label: "WhatsApp",
                    origin_category: "whatsapp",
                    financially_included: true,
                    status: "completed",
                },
            ],
        });

        await act(async () => {
            root.render(
                <SnapchatOrderSourceAudit
                    accountId="account-1"
                    dateFrom="2026-08-05"
                    dateTo="2026-08-05"
                />,
            );
        });
        await act(async () => Promise.resolve());

        expect(container.textContent).toContain("42");
        expect(container.textContent).toContain("10");
        expect(container.textContent).toContain("52");
        expect(container.textContent).toContain("48");
        expect(container.textContent).toContain("طلبات مرتبطة بحملة");
        expect(container.textContent).toContain("طلبات سناب بلا ربط تفصيلي");
        expect(container.textContent).toContain("قد يختلف عن جدول النشطة فقط");

        const openButton = container.querySelector('[data-testid="open-snapchat-order-audit"]');
        expect(openButton).not.toBeNull();
        await act(async () => openButton.click());

        expect(document.body.querySelector('[data-testid="snapchat-order-audit-dialog"]')).not.toBeNull();
        expect(document.body.textContent).toContain("1001");
        expect(document.body.textContent).toContain("1002");
        expect(document.body.textContent).toContain("حملة الرياض");
        expect(document.body.textContent).toContain("WhatsApp");
    });

    test("requests the exact selected account and date range", async () => {
        getSnapchatOrderSourceAudit.mockResolvedValue({ summary: {}, orders: [] });
        await act(async () => {
            root.render(
                <SnapchatOrderSourceAudit
                    accountId="account-2"
                    dateFrom="2026-08-01"
                    dateTo="2026-08-05"
                />,
            );
        });
        await act(async () => Promise.resolve());

        expect(getSnapchatOrderSourceAudit).toHaveBeenCalledWith({
            accountId: "account-2",
            dateFrom: "2026-08-01",
            dateTo: "2026-08-05",
        });
    });
});
