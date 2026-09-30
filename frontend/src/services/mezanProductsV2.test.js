import api from "../lib/api";
import {
    listWorkspaceProducts,
    publishProductControlDraft,
    setProductPublishActivity,
    syncProductsV2,
    syncRecentProductsV2,
} from "./mezanProductsV2";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
    api.get.mockResolvedValue({ data: { items: [] } });
    api.post.mockResolvedValue({ data: { ok: true } });
    setProductPublishActivity("p-1", false);
});

test("sold missing-cost requests use the dedicated contract endpoint", async () => {
    await listWorkspaceProducts({
        page: 2,
        missingMezanCost: true,
        soldOnly: true,
        productIds: "p-1,p-2",
    });

    expect(api.get).toHaveBeenCalledWith(
        "/products-v2/workspace/sold-missing-cost-products",
        { params: expect.objectContaining({
            page: 2,
            missing_mezan_cost: true,
            sold_only: true,
            product_ids: "p-1,p-2",
        }) },
    );
});

test("ordinary product browsing keeps using the generic endpoint", async () => {
    await listWorkspaceProducts({ page: 2 });

    expect(api.get).toHaveBeenCalledWith(
        "/products-v2/workspace/products",
        { params: expect.objectContaining({
            page: 2,
            missing_mezan_cost: false,
            sold_only: false,
        }) },
    );
});

test("workspace list is a pure local read and never waits for hidden provider sync", async () => {
    await listWorkspaceProducts({ page: 1, sort: "newest" });

    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).toHaveBeenCalledTimes(1);
});

test("parallel recent sync requests are single-flight", async () => {
    let resolveSync;
    api.post.mockReturnValue(new Promise((resolve) => { resolveSync = resolve; }));

    const first = syncRecentProductsV2({ force: true });
    const second = syncRecentProductsV2({ force: true });

    expect(api.post).toHaveBeenCalledTimes(1);
    resolveSync({ data: { ok: true } });
    await expect(Promise.all([first, second])).resolves.toEqual([{ ok: true }, { ok: true }]);
});

test("recent sync is suppressed while a product publish outcome is active", async () => {
    setProductPublishActivity("p-1", true);

    await expect(syncRecentProductsV2({ force: true })).resolves.toEqual({
        skipped: true,
        reason: "active_product_publish",
    });
    expect(api.post).not.toHaveBeenCalled();
});

test("parallel publish clicks share one HTTP request", async () => {
    let resolvePublish;
    api.post.mockReturnValue(new Promise((resolve) => { resolvePublish = resolve; }));

    const first = publishProductControlDraft("p-1", "draft-1");
    const second = publishProductControlDraft("p-1", "draft-1");

    expect(api.post).toHaveBeenCalledTimes(1);
    resolvePublish({ data: { status: "succeeded", attempt: { status: "succeeded" } } });
    await expect(Promise.all([first, second])).resolves.toHaveLength(2);
});

test("full catalogue sync is also suppressed while publish reconciliation is active", async () => {
    setProductPublishActivity("p-1", true);

    await expect(syncProductsV2()).resolves.toEqual({
        skipped: true,
        reason: "active_product_publish",
    });
    expect(api.post).not.toHaveBeenCalled();
});
