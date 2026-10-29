import api from "../lib/api";
import fixture from "./__fixtures__/googleLinkedCampaignEvidence.json";
import { linkedCampaignScope, validateLinkedCampaignEvidence, getGoogleLinkedCampaignEvidence } from "./googleLinkedCampaignEvidence";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const clone = (value) => JSON.parse(JSON.stringify(value));
const scope = () => linkedCampaignScope(fixture.plan);
beforeEach(() => api.get.mockReset());

test("accepts actual backend output once for two products and preserves the late association gap", () => {
    const data = validateLinkedCampaignEvidence(clone(fixture.response), scope());
    expect(data.campaign_contexts).toHaveLength(1);
    expect(data.products[0].context_refs).toEqual(data.products[1].context_refs);
    expect(data.products[0].associations.find((a) => a.event_id === "late-123")).toMatchObject({ observed_context_days: 0, metrics_status: "no_qualified_historical_context" });
});

test("request binds only saved plan/revision and cancellation signal", async () => {
    api.get.mockResolvedValue({ data: fixture.response });
    const controller = new AbortController();
    await getGoogleLinkedCampaignEvidence(fixture.plan, { signal: controller.signal });
    expect(api.get).toHaveBeenCalledWith("/integrations-v2/google_ads/commercial-plans/winter/linked-campaign-evidence", { params: { expected_revision: 3 }, signal: controller.signal });
});

test("scope follows the commercial-plan identifier contract and excludes explicit exclusions", () => {
    expect(linkedCampaignScope({ plan_id: "winter", revision: 3, fields: { pinned_product_ids: ["stored-product", "123"], excluded_product_ids: ["123"] } }).productIds).toEqual(["stored-product"]);
    expect(() => linkedCampaignScope({ ...fixture.plan, revision: 0 })).toThrow();
});

test.each(["foreignplan", "revision", "product", "duplicateproduct", "duplicatecontext", "foreignref", "foreignproof", "missingref", "allocatedmetrics", "authority", "coverage", "catalog", "profit", "storeorders", "livefresh", "metriczero", "provenance", "future", "lateassociation", "windowcount", "missingdates", "windowcurrency", "aggregate", "growth"])("rejects malformed %s without upgrading partial evidence", (kind) => {
    const data = clone(fixture.response), context = data.campaign_contexts[0], product = data.products[0], window = data.campaign_windows[0];
    if (kind === "foreignplan") data.plan_id = "other";
    if (kind === "revision") data.plan_revision = 4;
    if (kind === "product") product.product_id = "other";
    if (kind === "duplicateproduct") data.products[1] = clone(product);
    if (kind === "duplicatecontext") data.campaign_contexts.push(clone(context));
    if (kind === "foreignref") product.context_refs = ["b".repeat(64)];
    if (kind === "foreignproof") context.association_proofs[0].product_id = "other";
    if (kind === "missingref") product.context_refs = [];
    if (kind === "allocatedmetrics") product.metrics = context.metrics;
    if (kind === "authority") context.execution_authorized = true;
    if (kind === "coverage") window.source_coverage_complete = true;
    if (kind === "catalog") data.catalog_identity_verified = true;
    if (kind === "profit") context.profitability = 30;
    if (kind === "storeorders") data.independent_store_orders = 3;
    if (kind === "livefresh") context.live_freshness_verified = true;
    if (kind === "metriczero") context.metrics.provider_reported_purchases = 0;
    if (kind === "provenance") context.metric_provenance.spend_native = "verified";
    if (kind === "future") context.snapshot_finished_at = "2026-10-30T01:00:00Z";
    if (kind === "lateassociation") product.associations.find((a) => a.event_id === "late-123").observed_context_days = 1;
    if (kind === "windowcount") window.observed_context_days = 2;
    if (kind === "missingdates") window.missing_dates = [];
    if (kind === "windowcurrency") window.currency = "USD";
    if (kind === "aggregate") window.metrics_aggregated = true;
    if (kind === "growth") window.comparison_status = "growth_verified";
    expect(() => validateLinkedCampaignEvidence(data, scope())).toThrow();
});

test("null source metrics remain unknown and are never replaced by zero", () => {
    const data = clone(fixture.response), context = data.campaign_contexts[0];
    context.metrics.provider_reported_purchases = null;
    context.metric_provenance.provider_reported_purchases = "zero_or_source_omission_unproved";
    expect(validateLinkedCampaignEvidence(data, scope()).campaign_contexts[0].metrics.provider_reported_purchases).toBeNull();
});

test("empty saved selection accepts the backend's bounded empty result", () => {
    const data = clone(fixture.response);
    data.products = []; data.campaign_contexts = []; data.campaign_windows = [];
    expect(validateLinkedCampaignEvidence(data, { ...scope(), productIds: [] }).products).toEqual([]);
});
