import api from "../lib/api";
import fixture from "./__fixtures__/googleNativeInventoryPreview.json";
import { nativeInventoryScope, validateNativeInventoryPreview, getGoogleNativeInventoryPreview } from "./googleNativeInventoryPreview";
jest.mock("../lib/api", () => ({ __esModule: true, default: { post: jest.fn() } }));
const clone = (value) => JSON.parse(JSON.stringify(value));
const selected = ["1", "2", "3"];
const scope = () => nativeInventoryScope(fixture.plan, fixture.category, selected);
beforeEach(() => api.post.mockReset());

test("accepts actual PUT then fresh category and inventory reader output with separate parent and variant stock", () => {
    const data = validateNativeInventoryPreview(clone(fixture.response), scope());
    expect(data.products[0].parent_stock).toMatchObject({ quantity: "0", unlimited: null, state: "zero_unlimited_unknown" });
    expect(data.products[0].variants[0].stock).toMatchObject({ quantity: "2", state: "positive" });
    expect(data.products[1].parent_stock).toMatchObject({ quantity: null, unlimited: true, state: "positive" });
    expect(data.products[2].simple_product_observed).toBe(true);
});
test("explicit POST binds only saved revision category selected IDs and signal without write acknowledgement", async () => {
    api.post.mockResolvedValue({ data: fixture.response }); const controller = new AbortController();
    await getGoogleNativeInventoryPreview(fixture.plan, fixture.category, selected, { signal: controller.signal });
    expect(api.post).toHaveBeenCalledWith("/integrations-v2/google_ads/commercial-plans/seasonal-focus/native-inventory-preview", { expected_revision: 3, category_id: "8", selected_product_ids: selected }, { signal: controller.signal });
    expect(fixture.plan.fields).not.toHaveProperty("acknowledge_review"); expect(fixture.plan.fields).not.toHaveProperty("expected_revision");
});
test.each([[], ["1", "1"], ["99"], ["01"], ["1", "2", "3", "4", "5", "6"]].map((ids) => [ids]))("invalid selection %j is rejected before POST", async (ids) => {
    await expect(getGoogleNativeInventoryPreview(fixture.plan, fixture.category, ids)).rejects.toThrow(); expect(api.post).not.toHaveBeenCalled();
});
test("excluded and unresolved exclusion identities cannot be sent for native inventory", () => {
    for (const excluded of ["1", "internal-product"]) {
        const plan = clone(fixture.plan), category = clone(fixture.category); plan.fields.excluded_product_ids = [excluded];
        category.products[0].excluded = excluded === "1"; category.unresolved_exclusion_ids = excluded === "1" ? [] : [excluded];
        expect(() => nativeInventoryScope(plan, category, ["1"])).toThrow();
    }
});
test.each(["foreign", "revision", "store", "category", "selection", "missing", "duplicate", "authority", "mapping", "preparation", "global", "size", "atomic", "providerrevision", "eligibility", "variantpurchase", "variantcoverage", "stockstate", "stockquantity", "stockflag", "duplicatevariant", "labels", "blockers", "simple", "pages", "digest", "observed"])("rejects malformed %s response", (kind) => {
    const data = clone(fixture.response), product = data.products[0], variant = product.variants[0];
    if (kind === "foreign") data.plan_id = "other";
    if (kind === "revision") data.plan_revision++;
    if (kind === "store") data.store_id = "10";
    if (kind === "category") data.category_id = "9";
    if (kind === "selection") data.selected_product_ids.reverse();
    if (kind === "missing") data.products.pop();
    if (kind === "duplicate") data.products[1].salla_product_id = "1";
    if (kind === "authority") data.execution_authorized = true;
    if (kind === "mapping") product.merchant_mapping_verified = true;
    if (kind === "preparation") product.preparation_time = 2;
    if (kind === "global") product.globally_ready = true;
    if (kind === "size") data.size_coverage_verified = true;
    if (kind === "atomic") product.remote_snapshot_atomic = true;
    if (kind === "providerrevision") data.provider_revision = 1;
    if (kind === "eligibility") product.eligible_for_ad_selection = true;
    if (kind === "variantpurchase") variant.purchasability_verified = true;
    if (kind === "variantcoverage") variant.size_coverage_verified = true;
    if (kind === "stockstate") product.parent_stock.state = "zero";
    if (kind === "stockquantity") variant.stock.quantity = "NaN";
    if (kind === "stockflag") variant.stock.unlimited = "false";
    if (kind === "duplicatevariant") product.variants.push(clone(variant));
    if (kind === "labels") variant.source_option_labels.push(clone(variant.source_option_labels[0]));
    if (kind === "blockers") product.blocking_reasons = [];
    if (kind === "simple") product.simple_product_observed = true;
    if (kind === "pages") product.native_variant_page_digests = [];
    if (kind === "digest") data.category_response_digest = "bad";
    if (kind === "observed") product.observed_at = "2000-01-01T00:00:00Z";
    expect(() => validateNativeInventoryPreview(data, scope())).toThrow();
});
test("fresh category digest and partial variant/category traversal are allowed without upgrading proof", () => {
    const data = clone(fixture.response); data.category_response_digest = "a".repeat(64); data.category_traversal_complete = false;
    data.products[0].variant_traversal_complete = false; data.products[0].variants[0].source_option_labels = null;
    data.products[0].blocking_reasons = data.products[0].blocking_reasons.filter((value) => value !== "native_option_value_out_of_stock");
    expect(validateNativeInventoryPreview(data, scope()).globally_ready).toBe(false);
});
