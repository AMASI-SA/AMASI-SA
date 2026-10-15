import {
    createLatestResponseBroker,
    dashboardRequestRangeKey,
} from "./dashboardLatestResponseBroker";


test("builds a stable key from Dashboard date and filter params", () => {
    expect(dashboardRequestRangeKey({
        url: "/dashboard-v2?to_date=2026-08-04&from_date=2026-08-04",
        params: { payment_methods: "mada", shipping_companies: "smsa" },
    })).toBe(
        "from_date=2026-08-04&to_date=2026-08-04&payment_methods=mada&shipping_companies=smsa",
    );
});


test("an older Dashboard response waits for and resolves to the newest date", async () => {
    const broker = createLatestResponseBroker();
    const today = broker.begin({ rangeKey: "today" });
    const yesterday = broker.begin({ rangeKey: "yesterday" });

    const oldResult = broker.resolve(today, { data: { period: "today" } });
    const latestResponse = { data: { period: "yesterday" } };
    const latestResult = broker.resolve(yesterday, latestResponse);

    await expect(latestResult).resolves.toBe(latestResponse);
    await expect(oldResult).resolves.toBe(latestResponse);
});


test("an older response arriving after the latest response reuses the latest payload", async () => {
    const broker = createLatestResponseBroker();
    const today = broker.begin({ rangeKey: "today" });
    const yesterday = broker.begin({ rangeKey: "yesterday" });
    const latestResponse = { data: { orders: 130, period: "yesterday" } };

    await broker.resolve(yesterday, latestResponse);

    await expect(
        broker.resolve(today, { data: { orders: 3, period: "today" } }),
    ).resolves.toBe(latestResponse);
});


test("a stale request failure waits for the newest successful Dashboard response", async () => {
    const broker = createLatestResponseBroker();
    const today = broker.begin({ rangeKey: "today" });
    const yesterday = broker.begin({ rangeKey: "yesterday" });

    const staleFailure = broker.reject(today, new Error("old request failed"));
    const latestResponse = { data: { period: "yesterday" } };
    await broker.resolve(yesterday, latestResponse);

    await expect(staleFailure).resolves.toBe(latestResponse);
});
