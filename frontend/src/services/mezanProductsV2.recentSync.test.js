jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { post: jest.fn(), get: jest.fn() },
}));

let api, service, now;
const success = { ok: true, skipped: false, created: 1, updated: 0 };
beforeEach(() => {
    jest.resetModules();
    api = require("../lib/api").default;
    service = require("./mezanProductsV2");
    now = Date.parse("2026-11-11T12:00:00Z");
    jest.spyOn(Date, "now").mockImplementation(() => now);
    api.post.mockResolvedValue({ data: success });
    api.get.mockResolvedValue({ data: { items: [] } });
});
afterEach(() => jest.restoreAllMocks());

test("ordinary owner refreshes share an hour and forward an automatic bounded request", async () => {
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    now += 3_599_999;
    expect(await service.syncRecentProductsV2({ ownerId: "owner-a" })).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(1);
    now += 1;
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[0]).toEqual([
        "/products-v2/sync-recent", undefined,
        { params: { force: false }, timeout: 105_000, _mezanAuthDeadlineAt: Date.parse("2026-11-11T12:00:00Z") + 105_000 },
    ]);
});

test("a deliberate force bypasses successful hourly suppression", async () => {
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    await service.syncRecentProductsV2({ ownerId: "owner-a", force: true });
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][2].params).toEqual({ force: true });
});

test("same owner joins an active request, while a different owner cannot inherit it", async () => {
    let finishA, finishB;
    api.post.mockImplementationOnce(() => new Promise(resolve => { finishA = resolve; }));
    api.post.mockImplementationOnce(() => new Promise(resolve => { finishB = resolve; }));
    const first = service.syncRecentProductsV2({ ownerId: "owner-a" });
    const join = service.syncRecentProductsV2({ ownerId: "owner-a", force: true });
    const second = service.syncRecentProductsV2({ ownerId: "owner-b" });
    expect(api.post).toHaveBeenCalledTimes(2);
    finishB({ data: success });
    await second;
    finishA({ data: { ...success, created: 2 } });
    await Promise.all([first, join]);
    expect(await service.syncRecentProductsV2({ ownerId: "owner-b" })).toBeNull();
    expect(api.post).toHaveBeenCalledTimes(2);
});

test("account changes and identity-free callers rely on owner-scoped server admission", async () => {
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    await service.syncRecentProductsV2({ ownerId: "owner-b" });
    await service.syncRecentProductsV2();
    await service.syncRecentProductsV2();
    expect(api.post).toHaveBeenCalledTimes(4);
});

test("transport failures back off briefly without claiming a successful hour", async () => {
    api.post.mockRejectedValueOnce(new Error("unavailable"));
    await expect(service.syncRecentProductsV2({ ownerId: "owner-a" })).rejects.toThrow("unavailable");
    now += 59_999;
    expect(await service.syncRecentProductsV2({ ownerId: "owner-a" })).toBeNull();
    now += 1;
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    expect(api.post).toHaveBeenCalledTimes(2);
});

test.each([
    { ok: false, skipped: true, reason: "in_progress" },
    { ok: false, skipped: true, reason: "retry_later" },
    { ok: false, skipped: false, errors_count: 1 },
])("in-progress, skipped and partial results do not acquire an hourly success timestamp: %s", async result => {
    api.post.mockResolvedValueOnce({ data: result });
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    now += 60_000;
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    expect(api.post).toHaveBeenCalledTimes(2);
});

test("server up-to-date result preserves only its remaining cadence, not a new hour", async () => {
    api.post.mockResolvedValueOnce({ data: {
        ok: true, skipped: true, reason: "up_to_date",
        next_retry_at: new Date(now + 120_000).toISOString(),
    } });
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    now += 119_999;
    expect(await service.syncRecentProductsV2({ ownerId: "owner-a" })).toBeNull();
    now += 1;
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    expect(api.post).toHaveBeenCalledTimes(2);
});

test("catalogue listing remains automatic, and full manual sync is not throttled", async () => {
    await service.syncRecentProductsV2({ ownerId: "owner-a" });
    await service.listWorkspaceProducts();
    expect(api.post.mock.calls[1][2].params.force).toBe(false);
    expect(api.get.mock.calls[0][0]).toBe("/products-v2/workspace/products");
    await service.syncProductsV2();
    await service.syncProductsV2();
    expect(api.post.mock.calls.slice(2).map(call => call[0])).toEqual([
        "/products-v2/sync", "/products-v2/sync",
    ]);
});
