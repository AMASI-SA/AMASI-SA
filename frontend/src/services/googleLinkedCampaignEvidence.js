import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

const WINDOWS = { last14: [14, 0], previous14: [14, 14], last30: [30, 0], last90: [90, 0] };
const METRICS = ["spend_native", "provider_reported_purchases", "provider_reported_purchase_value_native"];
const PROVIDERS = ["snapchat_ads", "meta", "tiktok"];
const text = (value, limit = 32768) => typeof value === "string" && value.length > 0 && value.length <= limit && value.trim() === value;
const list = (value, limit) => Array.isArray(value) && value.length <= limit;
const same = (a, b) => a.length === b.length && a.every((value, index) => value === b[index]);
const unique = (values) => new Set(values).size === values.length;
const scopeKey = (row) => JSON.stringify([row.provider, row.account_id, row.campaign_id, row.currency, row.timezone, row.attribution_key]);
const campaignKey = (row) => JSON.stringify([row.provider, row.account_id, row.campaign_id]);
const instant = (value) => typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && !value.endsWith("-00:00") && Number.isFinite(Date.parse(value));
function unproved() { const error = new Error("تعذر إثبات مطابقة سياق الحملات للخطة المحفوظة."); error.code = "GOOGLE_LINKED_CAMPAIGN_EVIDENCE_UNPROVED"; return error; }
function requireProof(condition) { if (!condition) throw unproved(); }
function noAttribution(row) { return row.product_attribution_verified === false && row.independent_store_orders === null && row.profitability === null && row.execution_authorized === false; }
function nativeDay(value, timezone) {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(value));
    return ["year", "month", "day"].map((type) => parts.find((part) => part.type === type).value).join("-");
}
function shiftDay(value, days) { return new Date(Date.parse(`${value}T00:00:00Z`) + days * 86400000).toISOString().slice(0, 10); }
function windowDates(asOf, timezone, name) {
    const [count, offset] = WINDOWS[name], end = shiftDay(nativeDay(asOf, timezone), -1 - offset);
    return Array.from({ length: count }, (_, index) => shiftDay(end, index - count + 1));
}

export function linkedCampaignScope(plan) {
    const pinned = plan?.fields?.pinned_product_ids, excluded = plan?.fields?.excluded_product_ids;
    requireProof(typeof plan?.plan_id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(plan.plan_id)
        && Number.isSafeInteger(plan.revision) && plan.revision > 0 && list(pinned, 100) && list(excluded, 100)
        && [...pinned, ...excluded].every((id) => text(id, 240)) && unique(pinned) && unique(excluded));
    return { planId: plan.plan_id, revision: plan.revision, productIds: pinned.filter((id) => !excluded.includes(id)) };
}

export function validateLinkedCampaignEvidence(data, scope) {
    try {
        requireProof(data?.plan_id === scope.planId && data.plan_revision === scope.revision && data.status === "bounded_campaign_context_review"
            && data.snapshot_immutable === false && data.catalog_identity_verified === false && instant(data.as_of)
            && data.source_coverage_complete === false && noAttribution(data)
            && list(data.products, 100) && same(data.products.map((p) => p?.product_id), scope.productIds)
            && list(data.campaign_contexts, 570) && list(data.campaign_windows, 2280));
        const contexts = new Map(), identities = new Set(), groups = new Map(), campaignScopes = new Set();
        const selectedProducts = new Set(scope.productIds), productRefs = new Map(scope.productIds.map((id) => [id, []])), associationDays = new Map(), dateCache = new Map();
        const datesFor = (zone, name) => {
            const key = `${zone}|${name}`;
            if (!dateCache.has(key)) dateCache.set(key, windowDates(data.as_of, zone, name));
            return dateCache.get(key);
        };
        const associationKey = (row, productId, eventId) => JSON.stringify([row.provider, row.account_id, row.campaign_id, productId, eventId]);
        for (const row of data.campaign_contexts) {
            requireProof(row?.provider === "snapchat_ads" && text(row.account_id) && text(row.campaign_id) && /^[A-Z]{3}$/.test(row.currency)
                && text(row.timezone) && text(row.attribution_key) && /^[a-f0-9]{64}$/.test(row.context_id) && !contexts.has(row.context_id)
                && typeof row.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(row.date) && noAttribution(row)
                && row.live_freshness_verified === false && row.source_collection === "mezan_snapchat_committed_totals_v1"
                && ["conversion", "impression"].includes(row.action_report_time) && text(row.source_run_id)
                && row.attribution_windows && typeof row.attribution_windows === "object" && !Array.isArray(row.attribution_windows)
                && Object.keys(row.attribution_windows).length > 0 && Object.entries(row.attribution_windows).every(([key, value]) => text(key) && text(value))
                && [row.window_start_utc, row.window_end_utc, row.observed_at, row.snapshot_finished_at].every(instant));
            const start = Date.parse(row.window_start_utc), end = Date.parse(row.window_end_utc), observed = Date.parse(row.observed_at), finished = Date.parse(row.snapshot_finished_at), asOf = Date.parse(data.as_of);
            requireProof(start < end && end <= observed && observed <= finished && finished <= asOf
                && nativeDay(start, row.timezone) === row.date && nativeDay(end, row.timezone) === shiftDay(row.date, 1)
                && typeof row.snapshot_age_seconds === "number" && Number.isFinite(row.snapshot_age_seconds) && row.snapshot_age_seconds >= 0
                && Math.abs(row.snapshot_age_seconds - (asOf - finished) / 1000) < 0.002);
            // Age is displayed evidence, never an invented live-freshness cutoff.
            const identity = `${scopeKey(row)}|${row.date}`;
            requireProof(!identities.has(identity)); identities.add(identity);
            requireProof(row.metrics && row.metric_provenance && same(Object.keys(row.metrics).sort(), [...METRICS].sort())
                && same(Object.keys(row.metric_provenance).sort(), [...METRICS].sort()));
            for (const key of METRICS) {
                const value = row.metrics[key], provenance = row.metric_provenance[key];
                requireProof(value === null ? provenance === "zero_or_source_omission_unproved" : typeof value === "number" && Number.isFinite(value) && value > 0
                    && (key !== "provider_reported_purchases" || Number.isSafeInteger(value)) && provenance === "positive_saved_source_observation");
            }
            requireProof(list(row.association_proofs, 500) && row.association_proofs.length > 0
                && row.association_proofs.every((p) => selectedProducts.has(p?.product_id) && text(p.event_id))
                && unique(row.association_proofs.map((p) => JSON.stringify([p.product_id, p.event_id])))
                && Number.isSafeInteger(row.associated_product_count) && row.associated_product_count >= new Set(row.association_proofs.map((p) => p.product_id)).size
                && row.associated_product_count <= 500);
            const expectedWindows = Object.keys(WINDOWS).filter((name) => datesFor(row.timezone, name).includes(row.date));
            requireProof(list(row.window_ids, 4) && expectedWindows.length > 0 && same(row.window_ids, expectedWindows));
            for (const productId of new Set(row.association_proofs.map((proof) => proof.product_id))) productRefs.get(productId).push(row.context_id);
            for (const proof of row.association_proofs) {
                const key = associationKey(row, proof.product_id, proof.event_id);
                associationDays.set(key, (associationDays.get(key) || 0) + 1);
            }
            contexts.set(row.context_id, row); campaignScopes.add(campaignKey(row));
            const key = scopeKey(row); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row);
        }
        let totalAssociations = 0;
        for (const product of data.products) {
            requireProof(same(Object.keys(product).sort(), ["associations", "context_refs", "product_id", "status"])
                && list(product.context_refs, 570) && unique(product.context_refs) && list(product.associations, 3000));
            const expectedRefs = productRefs.get(product.product_id);
            requireProof(same(product.context_refs, expectedRefs));
            requireProof(product.status === (expectedRefs.length ? "partial_saved_campaign_context" : product.associations.length ? "verified_association_only" : "no_verified_current_campaign_association"));
            const seen = new Set(); totalAssociations += product.associations.length;
            for (const association of product.associations) {
                requireProof(PROVIDERS.includes(association?.provider) && text(association.account_id) && text(association.campaign_id) && text(association.event_id)
                    && (association.source === null || text(association.source)) && association.meaning === "intended_product_association_only"
                    && association.feed_mapping_verified === false && list(association.missing, 10) && association.missing.every((value) => text(value)));
                const key = `${campaignKey(association)}|${association.event_id}`;
                requireProof(!seen.has(key)); seen.add(key); campaignScopes.add(campaignKey(association));
                if (association.provider === "snapchat_ads") {
                    const count = associationDays.get(associationKey(association, product.product_id, association.event_id)) || 0;
                    requireProof(association.observed_context_days === count && association.source_coverage_complete === false
                        && Number.isSafeInteger(association.rejected_source_snapshots) && association.rejected_source_snapshots >= 0 && association.rejected_source_snapshots <= 95
                        && association.metrics_status === (count ? "partial_saved_campaign_context" : "no_qualified_historical_context"));
                } else requireProof(association.metrics_status === "source_fields_unproved" && association.missing.length > 0);
            }
        }
        requireProof(totalAssociations <= 3000 && campaignScopes.size <= 6);
        const windowsSeen = new Set();
        for (const window of data.campaign_windows) {
            requireProof(window && Object.hasOwn(WINDOWS, window.window_id) && window.source_coverage_complete === false
                && window.metrics_aggregated === false && window.comparison_status === "partial_context_only_no_growth_inference");
            const group = groups.get(scopeKey(window)), key = `${scopeKey(window)}|${window.window_id}`;
            requireProof(group && !windowsSeen.has(key)); windowsSeen.add(key);
            const dates = datesFor(window.timezone, window.window_id), selected = group.filter((row) => dates.includes(row.date));
            const refs = selected.map((row) => row.context_id), missing = dates.filter((day) => !selected.some((row) => row.date === day));
            requireProof(window.date_from === dates[0] && window.date_to === dates[dates.length - 1] && window.expected_days === dates.length
                && window.observed_context_days === refs.length && list(window.context_refs, 95) && same(window.context_refs, refs)
                && list(window.missing_dates, 90) && same(window.missing_dates, missing));
        }
        requireProof(windowsSeen.size === groups.size * 4);
        return data;
    } catch { throw unproved(); }
}

export async function getGoogleLinkedCampaignEvidence(plan, { signal } = {}) {
    const scope = linkedCampaignScope(plan);
    const response = await api.get(`${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}/linked-campaign-evidence`, { params: { expected_revision: scope.revision }, signal });
    return validateLinkedCampaignEvidence(response.data, scope);
}
