import {
    getMezanProductWorkspace,
    MEZAN_PRODUCT_PAGE_POLICY,
} from "./mezanProductCatalog";

test("products v2 is a read-only aggregate page", async () => {
    const workspace = await getMezanProductWorkspace();

    expect(MEZAN_PRODUCT_PAGE_POLICY.page_id).toBe("products-v2");
    expect(workspace.page_policy.mode).toBe("read_only_aggregate");
    expect(workspace.page_policy.writes_enabled).toBe(false);
    expect(workspace.meta.writes_enabled).toBe(false);
});

test("each operational product workflow belongs to its own future page", async () => {
    const workspace = await getMezanProductWorkspace();
    const sources = workspace.page_policy.operational_sources;

    expect(sources.map((source) => source.id)).toEqual([
        "components",
        "purchase_invoices",
        "production_orders",
        "returns",
    ]);
    expect(sources.every((source) => source.page && source.description)).toBe(true);
});
