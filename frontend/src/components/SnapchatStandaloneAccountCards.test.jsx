import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { SnapchatStandaloneAccountCardsContent } from "./SnapchatStandaloneAccountCards";

const data = {
    today_date: "2026-08-01",
    month_start: "2026-08-01",
    accounts: [
        {
            id: "usd-account",
            external_account_id: "usd-account",
            name: "متجر أماسي Self Service",
            currency_native: "USD",
            report_timezone: "Asia/Riyadh",
            today: {
                spend: 1455.94,
                orders: 1,
                revenue: 332.32,
                roas: 0.23,
                cost_per_order: 1455.94,
            },
            month: {
                spend: 1455.94,
                orders: 1,
                revenue: 332.32,
                roas: 0.23,
                cost_per_order: 1455.94,
            },
        },
        {
            id: "sar-account",
            external_account_id: "sar-account",
            name: "متجر أماسي سعودي",
            currency_native: "SAR",
            report_timezone: "Asia/Riyadh",
            today: {
                spend: 0,
                orders: 1,
                revenue: 178.87,
                roas: 0,
                cost_per_order: null,
            },
            month: {
                spend: 0,
                orders: 1,
                revenue: 178.87,
                roas: 0,
                cost_per_order: null,
            },
        },
    ],
};

test("renders one full independent card for each selected Snapchat account", () => {
    const html = renderToStaticMarkup(
        <MemoryRouter>
            <SnapchatStandaloneAccountCardsContent
                data={data}
                onRefresh={() => {}}
            />
        </MemoryRouter>,
    );

    expect(html).toContain("حسابات Snapchat المنفصلة");
    expect(html).toContain("لا يوجد دمج بين الحسابات");
    expect(html).toContain("متجر أماسي Self Service");
    expect(html).toContain("متجر أماسي سعودي");
    expect((html.match(/data-testid=\"snapchat-standalone-account-/g) || []).length).toBe(2);
    expect(html).toContain("1,455.94 ر.س");
    expect(html).toContain("332.32 ر.س");
    expect(html).toContain("178.87 ر.س");
    expect(html).not.toContain("spend_share_pct");
    expect(html).not.toContain("100.0%");
});
