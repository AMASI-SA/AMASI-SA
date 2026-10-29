import api from "../lib/api";
import fixture from "./__fixtures__/googleLiveCategoryPreview.json";
import { liveCategoryScope, validateLiveCategoryPreview, getGoogleLiveCategoryPreview } from "./googleLiveCategoryPreview";
jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const clone = (value) => JSON.parse(JSON.stringify(value));
const scope = () => liveCategoryScope(fixture.plan, "8");
beforeEach(() => api.get.mockReset());

test("accepts real backend response with explicit exclusions and unproved inventory", () => {
    expect(validateLiveCategoryPreview(clone(fixture.response), scope()).products[0]).toMatchObject({ excluded: true, inventory: "unproved", eligible_for_ad_selection: false });
});
test("request binds saved ID revision and signal, never sends a URL to fetch", async () => {
    api.get.mockResolvedValue({ data: fixture.response }); const controller = new AbortController();
    await getGoogleLiveCategoryPreview(fixture.plan, { categoryId: "8", signal: controller.signal });
    expect(api.get).toHaveBeenCalledWith("/integrations-v2/google_ads/commercial-plans/category-winter/live-category-preview", { params: { expected_revision: 1, category_id: "8" }, signal: controller.signal });
});
test("URL-only uses the saved exact URL and no arbitrary URL query", async () => {
    const plan = { ...fixture.plan, fields: { ...fixture.plan.fields, category_ids: [], category_url: fixture.response.category_url } };
    api.get.mockResolvedValue({ data: fixture.response }); await getGoogleLiveCategoryPreview(plan);
    expect(api.get.mock.calls[0][1].params).toEqual({ expected_revision: 1 });
    expect(() => validateLiveCategoryPreview({ ...fixture.response, category_url: "https://other.test/winter" }, liveCategoryScope(plan))).toThrow();
});
test.each(["foreign", "stale", "category", "url", "atomic", "providerrevision", "inventory", "mapping", "authority", "eligible", "excluded", "unresolved", "duplicate", "membership", "hidden", "status", "count", "pages", "digest", "time", "source", "scope"])("rejects malformed %s response", (kind) => {
    const data = clone(fixture.response), row = data.products[0];
    if (kind === "foreign") data.plan_id = "other";
    if (kind === "stale") data.plan_revision++;
    if (kind === "category") data.category_id = "9";
    if (kind === "url") data.category_url = "javascript:alert(1)";
    if (kind === "atomic") data.remote_snapshot_atomic = true;
    if (kind === "providerrevision") data.provider_revision = 1;
    if (kind === "inventory") data.inventory_verified = true;
    if (kind === "mapping") data.mezan_product_mapping_verified = true;
    if (kind === "authority") data.execution_authorized = true;
    if (kind === "eligible") row.eligible_for_ad_selection = true;
    if (kind === "excluded") row.excluded = false;
    if (kind === "unresolved") data.unresolved_exclusion_ids = [];
    if (kind === "duplicate") data.products[1].salla_product_id = row.salla_product_id;
    if (kind === "membership") row.membership = "verified_all";
    if (kind === "hidden") row.category_hidden = true;
    if (kind === "status") row.product_status = "available";
    if (kind === "count") data.products_observed++;
    if (kind === "pages") data.pages_read = 5;
    if (kind === "digest") data.response_digest = "bad";
    if (kind === "time") data.observed_finished_at = "2000-01-01T00:00:00Z";
    if (kind === "source") data.source = "cached";
    if (kind === "scope") data.stored_scope_authority = "verified";
    expect(() => validateLiveCategoryPreview(data, scope())).toThrow();
});
test.each([true, false])("traversal %s still preserves unknown category and empty membership observation", (complete) => {
    const data = { ...clone(fixture.response), products: [], products_observed: 0, category_status: "unknown", pagination_traversal_complete: complete };
    expect(validateLiveCategoryPreview(data, scope()).remote_snapshot_atomic).toBe(false);
    data.products = [{ ...fixture.response.products[0], category_hidden: null }]; data.products_observed = 1;
    expect(validateLiveCategoryPreview(data, scope()).products[0].category_hidden).toBeNull();
});
test.each([0, -1, 1.5])("unsaved or invalid revision %s cannot call endpoint", async (revision) => {
    await expect(getGoogleLiveCategoryPreview({ ...fixture.plan, revision })).rejects.toThrow(); expect(api.get).not.toHaveBeenCalled();
});
test("only saved numeric categories can be selected; multiple categories require an explicit choice", () => {
    expect(() => liveCategoryScope(fixture.plan, "999")).toThrow();
    expect(() => liveCategoryScope({ ...fixture.plan, fields: { ...fixture.plan.fields, category_ids: ["8", "9"] } })).toThrow();
    expect(() => liveCategoryScope({ ...fixture.plan, fields: { ...fixture.plan.fields, category_ids: ["slug"] } }, "slug")).toThrow();
});
