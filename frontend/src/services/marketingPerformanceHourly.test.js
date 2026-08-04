import { normalizeSnapchatMarketingWorkspace } from "./marketingPerformance";

test("normalizes and orders Snapchat account-local hourly rows", () => {
    const result = normalizeSnapchatMarketingWorkspace({
        date_from: "2026-08-04",
        date_to: "2026-08-04",
        business_timezone: "Asia/Riyadh",
        totals: { spend_sar: 30, sales_sar: 100, orders: 2 },
        hourly: [
            {
                date: "2026-08-04",
                hour: "02:00",
                hour_index: 2,
                spend_sar: 20,
                sales_sar: 100,
                orders: 2,
                roas: 5,
                observed: true,
                is_future: false,
                result_source: "salla",
            },
            {
                date: "2026-08-04",
                hour: "00:00",
                hour_index: 0,
                spend_sar: 10,
                sales_sar: 0,
                orders: 0,
                observed: true,
                is_future: false,
                result_source: "salla",
            },
        ],
        source: {
            hourly_collection: "mezan_snapchat_performance_account_hour_v1",
            hourly_source_mode: "snapchat_account_campaign_breakdown_account_hour_v1",
            hourly_rows: 2,
            hourly_available: true,
        },
    });

    expect(result.hourly).toHaveLength(2);
    expect(result.hourly.map((row) => row.hour)).toEqual(["00:00", "02:00"]);
    expect(result.hourly[1]).toMatchObject({
        spend_sar: 20,
        sales_sar: 100,
        orders: 2,
        roas: 5,
        result_source: "salla",
    });
    expect(result.source).toMatchObject({
        hourly_collection: "mezan_snapchat_performance_account_hour_v1",
        hourly_rows: 2,
        hourly_available: true,
    });
});
