import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { SnapchatSeparatedAccountsView } from "./SnapchatAccountsCards";


jest.mock("react-router-dom", () => ({
    Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}));


test("renders one independent Mezan V2 Snapchat card per account", () => {
    const html = renderToStaticMarkup(
        <SnapchatSeparatedAccountsView
            data={{
                today: "2026-08-02",
                month_start: "2026-08-01",
                accounts: [
                    {
                        id: "snap-a",
                        name: "حساب سناب الأول",
                        currency: "USD",
                        timezone: "America/Los_Angeles",
                        today: {
                            date: "2026-08-02", spend: 100, orders: 2,
                            revenue: 300, roas: 3, cost_per_order: 50,
                        },
                        month: {
                            start: "2026-08-01", spend: 500, orders: 8,
                            revenue: 1200, roas: 2.4, cost_per_order: 62.5,
                        },
                    },
                    {
                        id: "snap-b",
                        name: "حساب سناب الثاني",
                        currency: "SAR",
                        timezone: "Asia/Riyadh",
                        today: {
                            date: "2026-08-02", spend: 20, orders: 1,
                            revenue: 90, roas: 4.5, cost_per_order: 20,
                        },
                        month: {
                            start: "2026-08-01", spend: 80, orders: 4,
                            revenue: 360, roas: 4.5, cost_per_order: 20,
                        },
                    },
                ],
            }}
            onRefresh={() => {}}
            refreshing={false}
        />,
    );

    expect(html).toContain("حسابات Snapchat المنفصلة");
    expect(html).toContain("لا يوجد دمج بين الحسابات");
    expect(html).toContain('data-testid="snap-v2-account-card-snap-a"');
    expect(html).toContain('data-testid="snap-v2-account-card-snap-b"');
    expect(html).toContain('data-testid="snap-v2-snap-a-today-spend"');
    expect(html).toContain('data-testid="snap-v2-snap-a-month-spend"');
    expect(html).toContain('data-testid="snap-v2-snap-b-today-spend"');
    expect(html).toContain('data-testid="snap-v2-snap-b-month-spend"');
    expect(html).toContain("حساب سناب الأول");
    expect(html).toContain("حساب سناب الثاني");
    expect(html).toContain("America/Los_Angeles");
    expect(html).toContain("Asia/Riyadh");
    expect(html).toContain("account=snap-a");
    expect(html).toContain("account=snap-b");
});
