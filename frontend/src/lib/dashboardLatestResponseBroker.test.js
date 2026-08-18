import {
    createDashboardRequestCoordinator,
    dashboardRequestRangeKey,
    stripDashboardCacheBuster,
} from "./dashboardLatestResponseBroker";


function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((onResolve, onReject) => {
        resolve = onResolve;
        reject = onReject;
    });
    return { promise, resolve, reject };
}


test("builds a stable dashboard key and ignores cache-busting timestamps", () => {
    expect(dashboardRequestRangeKey({
        method: "get",
        url: "/dashboard-v2?_refresh=123&to_date=2026-08-04&from_date=2026-08-04",
        params: { payment_methods: "mada", shipping_companies: "smsa" },
    })).toBe(
        "/dashboard-v2?from_date=2026-08-04&payment_methods=mada&shipping_companies=smsa&to_date=2026-08-04",
    );
    expect(stripDashboardCacheBuster({
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-04&_refresh=123",
    }).url).toBe("/dashboard-v2?from_date=2026-08-04");
});


test("shares one real request across concurrent callers for the same range", async () => {
    const coordinator = createDashboardRequestCoordinator();
    const network = deferred();
    const load = jest.fn(() => network.promise);
    const config = {
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-17&to_date=2026-08-17",
    };

    const first = coordinator.run(config, load);
    const second = coordinator.run(
        { ...config, url: `${config.url}&_refresh=999` },
        load,
    );
    await Promise.resolve();

    expect(load).toHaveBeenCalledTimes(1);
    expect(coordinator.snapshot()).toEqual(expect.objectContaining({
        inFlight: 1,
        sharedCallers: 1,
    }));

    const response = { data: { total_orders: 18 } };
    network.resolve(response);
    await expect(Promise.all([first, second])).resolves.toEqual([
        response,
        response,
    ]);
    expect(coordinator.snapshot().inFlight).toBe(0);
});


test("an older range chains to the newest real request without a waiter queue", async () => {
    const coordinator = createDashboardRequestCoordinator();
    const oldNetwork = deferred();
    const latestNetwork = deferred();

    const oldResult = coordinator.run(
        { method: "get", url: "/dashboard-v2?from_date=2026-08-16" },
        () => oldNetwork.promise,
    );
    const latestResult = coordinator.run(
        { method: "get", url: "/dashboard-v2?from_date=2026-08-17" },
        () => latestNetwork.promise,
    );

    oldNetwork.resolve({ data: { period: "old" } });
    const latestResponse = { data: { period: "latest" } };
    latestNetwork.resolve(latestResponse);

    await expect(oldResult).resolves.toBe(latestResponse);
    await expect(latestResult).resolves.toBe(latestResponse);
});


test("a stale request settles with the newest failure instead of hanging", async () => {
    const coordinator = createDashboardRequestCoordinator();
    const oldNetwork = deferred();
    const latestNetwork = deferred();

    const oldResult = coordinator.run(
        { method: "get", url: "/dashboard?from_date=2026-08-16" },
        () => oldNetwork.promise,
    );
    const latestResult = coordinator.run(
        { method: "get", url: "/dashboard?from_date=2026-08-17" },
        () => latestNetwork.promise,
    );

    oldNetwork.resolve({ data: { period: "old" } });
    const failure = new Error("latest request failed");
    latestNetwork.reject(failure);

    await expect(oldResult).rejects.toBe(failure);
    await expect(latestResult).rejects.toBe(failure);
    expect(coordinator.snapshot().inFlight).toBe(0);
});


test("old and V2 dashboard roots never share an in-flight request", async () => {
    const coordinator = createDashboardRequestCoordinator();
    const legacyLoad = jest.fn(async () => ({ data: { source: "legacy" } }));
    const v2Load = jest.fn(async () => ({ data: { source: "v2" } }));

    await Promise.all([
        coordinator.run({ method: "get", url: "/dashboard" }, legacyLoad),
        coordinator.run({ method: "get", url: "/dashboard-v2" }, v2Load),
    ]);

    expect(legacyLoad).toHaveBeenCalledTimes(1);
    expect(v2Load).toHaveBeenCalledTimes(1);
});
