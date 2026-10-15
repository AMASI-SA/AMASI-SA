import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { GoogleAnalyticsRealtimeContent } from "./GoogleAnalyticsRealtimeCards";

jest.mock("recharts", () => ({
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    BarChart: ({ children }) => <div>{children}</div>,
    Bar: () => <div data-testid="mock-bar" />,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
}));


test("renders the three GA4 realtime cards with Amasi facts", () => {
    const html = renderToStaticMarkup(
        <GoogleAnalyticsRealtimeContent
            data={{
                active_users: {
                    last_30_minutes: 122,
                    last_5_minutes: 12,
                    per_minute: [
                        { minutes_ago: 1, active_users: 7 },
                        { minutes_ago: 0, active_users: 4 },
                    ],
                },
                top_pages: [
                    { title: "عناية صيفية لسن المحير | متجر أماسي", views: 33 },
                    { title: "شنط كوتش تابي | متجر أماسي", views: 22 },
                ],
                key_events: [
                    { event_name: "add_to_cart", count: 2 },
                ],
            }}
        />,
    );

    expect(html).toContain("الصفحات الأكثر مشاهدة");
    expect(html).toContain("المستخدمون النشطون الآن");
    expect(html).toContain("الأحداث المهمة");
    expect(html).toContain("عناية صيفية لسن المحير");
    expect(html).toContain("122");
    expect(html).toContain("12");
    expect(html).toContain("add_to_cart");
});
