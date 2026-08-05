import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import SnapchatOrderSourceAudit from "./SnapchatOrderSourceAudit";
import { getSnapchatOrderSourceAudit } from "../../services/snapchatOrderSourceAudit";

jest.mock("../../services/snapchatOrderSourceAudit", () => ({
    getSnapchatOrderSourceAudit: jest.fn(),
}));

beforeEach(() => {
    getSnapchatOrderSourceAudit.mockReset();
});

test("shows Salla truth, non-campaign orders and platform attribution separately", async () => {
    getSnapchatOrderSourceAudit.mockResolvedValue({
        account: { account_name: "حساب الرياض" },
        summary: {
            campaign_matched_orders: 42,
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

    render(
        <SnapchatOrderSourceAudit
            accountId="account-1"
            dateFrom="2026-08-05"
            dateTo="2026-08-05"
        />,
    );

    await waitFor(() => expect(screen.getByText("42")).toBeInTheDocument());
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("52")).toBeInTheDocument();
    expect(screen.getByText("48")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("open-snapchat-order-audit"));
    expect(screen.getByTestId("snapchat-order-audit-dialog")).toBeInTheDocument();
    expect(screen.getByText("1001")).toBeInTheDocument();
    expect(screen.getByText("1002")).toBeInTheDocument();
    expect(screen.getByText("حملة الرياض")).toBeInTheDocument();
    expect(screen.getByText("WhatsApp")).toBeInTheDocument();
});

test("requests the exact selected account and date range", async () => {
    getSnapchatOrderSourceAudit.mockResolvedValue({ summary: {}, orders: [] });
    render(
        <SnapchatOrderSourceAudit
            accountId="account-2"
            dateFrom="2026-08-01"
            dateTo="2026-08-05"
        />,
    );
    await waitFor(() => expect(getSnapchatOrderSourceAudit).toHaveBeenCalledWith({
        accountId: "account-2",
        dateFrom: "2026-08-01",
        dateTo: "2026-08-05",
    }));
});
