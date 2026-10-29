import api from "../lib/api";
import fixture from "./__fixtures__/googleProductTestReview.json";
import { productTestReviewScope, validateProductTestReview, getProductFitState, saveProductFit, getProductTestReview } from "./googleProductTestReview";
jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));
const copy = (v) => JSON.parse(JSON.stringify(v));
const ids = fixture.selected_product_ids, root = "/integrations-v2/google_ads/commercial-plans/winter";
beforeEach(() => { api.get.mockReset(); api.put.mockReset(); api.post.mockReset(); api.get.mockResolvedValue({ data: copy(fixture.fit_state) }); api.put.mockResolvedValue({ data: copy(fixture.fit_saved) }); api.post.mockResolvedValue({ data: copy(fixture.candidate) }); });
test("actual authenticated management writer fixture matches strict GET CAS PUT and review POST", async () => {
    expect(fixture.plan.fields.acknowledge_review).toBeUndefined(); expect(fixture.plan.fields.expected_revision).toBeUndefined();
    const signal = new AbortController().signal;
    expect(await getProductFitState(fixture.plan, fixture.category, ids, { signal })).toEqual(fixture.fit_state);
    expect(api.get).toHaveBeenCalledWith(root + "/product-season-reviews", { params: { expected_revision: 1, category_id: "8", product_ids: ids }, signal });
    const saved = await saveProductFit(fixture.plan, fixture.category, ids, "1", 0, "suitable_for_test", fixture.fit_saved.reason, true, { signal });
    expect(saved.review_revision).toBe(1); expect(api.put.mock.calls[0][1]).toEqual({ expected_plan_revision: 1, expected_review_revision: 0, category_id: "8", decision: "suitable_for_test", reason: fixture.fit_saved.reason, acknowledge_owner_review: true });
    expect(await getProductTestReview(fixture.plan, fixture.category, ids, fixture.fit_state, { signal })).toEqual(fixture.candidate);
    expect(api.post).toHaveBeenCalledWith(root + "/product-test-review", { expected_revision: 1, category_id: "8", selected_product_ids: ids }, { signal });
});
test("server frozen windows do not follow client clock and source currencies remain separate", async () => {
    const clock = jest.spyOn(Date, "now").mockReturnValue(Date.UTC(2040, 1, 1));
    try { const out = await getProductTestReview(fixture.plan, fixture.category, ids, fixture.fit_state); expect(out.products[0].current_store_observations.currency_groups.map((g) => g.currency_code)).toEqual(["SAR", "USD"]); expect(out.campaign_contexts).toHaveLength(1); } finally { clock.mockRestore(); }
});
test.each(["excluded", "duplicate", "six", "revision", "category"])("unproved scope %s never dispatches", async (kind) => {
    const plan = copy(fixture.plan), category = copy(fixture.category); let selected = [...ids];
    if (kind === "excluded") selected = ["3"]; if (kind === "duplicate") selected = ["1", "1"]; if (kind === "six") selected = ["1", "2", "3", "4", "5", "6"];
    if (kind === "revision") plan.revision = 2; if (kind === "category") category.category_id = "9";
    await expect(getProductFitState(plan, category, selected)).rejects.toMatchObject({ code: "GOOGLE_PRODUCT_TEST_REVIEW_UNPROVED" }); expect(api.get).not.toHaveBeenCalled();
});
test.each([false, undefined])("fit write requires explicit acknowledgement %s", async (ack) => { await expect(saveProductFit(fixture.plan, fixture.category, ids, "1", 0, "suitable_for_test", "سبب", ack)).rejects.toMatchObject({ code: "GOOGLE_PRODUCT_TEST_REVIEW_UNPROVED" }); expect(api.put).not.toHaveBeenCalled(); });
test("unknown fit retains stale CAS revision, not automatic suitable", async () => { api.get.mockResolvedValue({ data: copy(fixture.password_stale_fit_state) }); const out = await getProductFitState(fixture.plan, fixture.category, ids); expect(out.products[0]).toMatchObject({ review_revision: 1, current_owner_review: false, decision: "unknown", reason: null }); });
test.each(["plan", "revision", "authority", "profit", "growth", "coverage", "conversion", "owner_fit", "decision", "false_reason", "stock", "clock", "period", "prior", "context_dup", "context_ref", "context_currency", "context_day", "missing_dates", "lane", "zero", "association", "paid"])("malformed review %s is rejected", async (kind) => {
    const out = copy(fixture.candidate), row = out.products[0];
    if (kind === "plan") out.plan_id = "other"; if (kind === "revision") out.plan_revision = 2;
    if (kind === "authority") out.execution_authorized = true; if (kind === "profit") row.profitability = 10; if (kind === "growth") out.growth = 1;
    if (kind === "coverage") out.source_coverage_complete = true; if (kind === "conversion") out.platform_conversions_added = true;
    if (kind === "owner_fit") row.season_fit.revision = 99; if (kind === "decision") row.decision = "blocked";
    if (kind === "false_reason") out.products[1].evidence_reasons.push("qualifying_saved_current_gross_lines");
    if (kind === "stock") row.inventory_observation.globally_ready = true; if (kind === "clock") out.native_observed_finished_at = "2099-01-01T00:00:00Z";
    if (kind === "period") out.current_windows.previous_14_days.from = "2020-01-01"; if (kind === "prior") out.prior_windows.owner_reviewed_prior_season.to = "2026-01-01";
    if (kind === "context_dup") out.campaign_contexts.push(copy(out.campaign_contexts[0])); if (kind === "context_ref") row.campaign_context_refs = ["f".repeat(64)];
    if (kind === "context_currency") out.campaign_windows[0].currency = "USD"; if (kind === "context_day") out.campaign_contexts[0].date = "2026-10-27";
    if (kind === "missing_dates") out.campaign_windows[0].missing_dates = []; if (kind === "lane") out.review_lanes[0].product_ids = [];
    if (kind === "zero") row.current_store_observations.currency_groups[0].windows.last_14_days.observed_orders = 0;
    if (kind === "association") out.products[1].campaign_associations[0].feed_mapping_verified = true; if (kind === "paid") row.paid_launch_eligible = true;
    api.post.mockResolvedValue({ data: out }); await expect(getProductTestReview(fixture.plan, fixture.category, ids, fixture.fit_state)).rejects.toMatchObject({ code: "GOOGLE_PRODUCT_TEST_REVIEW_UNPROVED" });
});
test("not suitable is blocked; source association-only never claims product performance", () => {
    const scope = productTestReviewScope(fixture.plan, fixture.category, ids);
    const out = validateProductTestReview(copy(fixture.blocked_candidate), scope, fixture.blocked_fit_state);
    expect(out.products[1].decision).toBe("blocked"); expect(out.products[1].campaign_associations.some((a) => a.provider === "meta" && a.metrics_status === "source_fields_unproved")).toBe(true);
});
test("newer fit revision invalidates candidate rather than using stale owner declaration", async () => { await expect(getProductTestReview(fixture.plan, fixture.category, ids, fixture.rereview_fit_state)).rejects.toMatchObject({ code: "GOOGLE_PRODUCT_TEST_REVIEW_UNPROVED" }); });
