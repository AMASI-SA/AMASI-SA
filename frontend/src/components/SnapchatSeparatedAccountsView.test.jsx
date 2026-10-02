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
    const container = document.createElement("div");
    container.innerHTML = html;
    expect(container.querySelectorAll("article")).toHaveLength(2);
    for (const [id, name, currency, timezone, today, month] of [
        ["snap-a", "حساب سناب الأول", "USD", "America/Los_Angeles", "100.00", "500.00"],
        ["snap-b", "حساب سناب الثاني", "SAR", "Asia/Riyadh", "20.00", "80.00"],
    ]) {
        const card = container.querySelector(`[data-testid="snap-v2-account-card-${id}"]`);
        expect(card.textContent).toContain(name);
        expect(card.textContent).toContain(currency);
        expect(card.textContent).toContain(timezone);
        expect(card.querySelector(`[data-testid="snap-v2-${id}-today-spend"] .num`).textContent).toBe(`${today} ر.س`);
        expect(card.querySelector(`[data-testid="snap-v2-${id}-month-spend"] .num`).textContent).toBe(`${month} ر.س`);
        expect(card.querySelector("a").getAttribute("href")).toBe(`/ads-manager?provider=snapchat&account=${id}`);
        expect(card.textContent).toContain("هذا الحساب فقط");
    }
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
