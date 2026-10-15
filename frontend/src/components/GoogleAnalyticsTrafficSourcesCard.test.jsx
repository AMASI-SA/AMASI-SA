import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { GoogleAnalyticsTrafficSourcesContent } from "./GoogleAnalyticsTrafficSourcesCard";


test("renders visits, orders, and revenue by independent GA4 source", () => {
    const html = renderToStaticMarkup(
        <GoogleAnalyticsTrafficSourcesContent
            periodKey="today"
            data={{
                today: {
                    sessions: 43,
                    orders: 9,
                    purchase_revenue: 1820.5,
                    sources: [
                        {
                            key: "snapchat",
                            platform: "snapchat",
                            label: "Snapchat",
                            sessions: 22,
                            active_users: 19,
                            orders: 5,
                            purchase_revenue: 1060.5,
                            raw_sources: ["snapchat", "snapchat.com"],
                        },
                        {
                            key: "tiktok",
                            platform: "tiktok",
                            label: "TikTok",
                            sessions: 9,
                            active_users: 8,
                            orders: 2,
                            purchase_revenue: 420,
                            raw_sources: ["tiktok"],
                        },
                        {
                            key: "meta",
                            platform: "meta",
                            label: "Meta / Instagram",
                            sessions: 5,
                            active_users: 4,
                            orders: 1,
                            purchase_revenue: 190,
                            raw_sources: ["fb"],
                        },
                    ],
                },
            }}
        />,
    );

    expect(html).toContain("المصدر");
    expect(html).toContain("الجلسات");
    expect(html).toContain("طلبات GA4");
    expect(html).toContain("مبيعات GA4");
    expect(html).toContain("Snapchat");
    expect(html).toContain("TikTok");
    expect(html).toContain("Meta / Instagram");
    expect(html).toContain("snapchat.com");
    expect(html).toContain("1,060.50");
});
