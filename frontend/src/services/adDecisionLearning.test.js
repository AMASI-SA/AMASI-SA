import api from "../lib/api";
import {
    diagnoseAdBusinessChange,
    getAdDecisionAccountSummaries,
    getAdDecisionHistory,
    normalizeAdDecision,
    normalizeAdDecisionPage,
    reviewAdaptiveSnapchat,
} from "./adDecisionLearning";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));

describe("adDecisionLearning", () => {
    beforeEach(() => {
        api.get.mockReset();
        api.post.mockReset();
    });

    test("marks a directly observed Snapchat change without inventing a reason", () => {
        expect(normalizeAdDecision({
            id: "change-1",
            source: "provider_observed",
            evidence_windows: { "14d": { roas: 2.1 }, "3d": { roas: 4.2 } },
        })).toMatchObject({
            decision_id: "change-1",
            direct_snapchat: true,
            reason: null,
            reason_unrecorded: true,
            evidence: { windows: { "14": { roas: 2.1 }, "3": { roas: 4.2 } } },
        });
    });

    test("normalizes flexible ledger pagination", () => {
        expect(normalizeAdDecisionPage({
            entries: [{ entry_id: "one" }],
            meta: { page: 2, pages: 4, total: 17, limit: 5 },
        })).toEqual({
            items: [expect.objectContaining({ decision_id: "one" })],
            pagination: { page: 2, pages: 4, total: 17, limit: 5 },
        });
    });

    test("keeps later annotations and derives the entity name from snapshots", () => {
        expect(normalizeAdDecision({
            id: "annotated",
            before: { name: "حملة المشط" },
            annotations: [{ annotation_id: "note-1", text: "حقق الهدف بعد 3 أيام." }],
        })).toMatchObject({
            entity_name: "حملة المشط",
            annotations: [{ id: "note-1", text: "حقق الهدف بعد 3 أيام." }],
        });
    });

    test("normalizes expected checks, all measured scopes, and post-decision attribution", () => {
        const decision = normalizeAdDecision({
            id: "change-measured",
            latest_evaluation: {
                outcome_status: "successful",
                expected_vs_actual: {
                    checks: [{ scope: "campaign", metric: "sales_sar", direction: "increase", met: true }],
                },
                campaign_delta: { sales_sar: { actual: 140, delta_pct: 40, direction: "increase" } },
                account_delta: { orders: { actual: 12, delta_pct: 20, direction: "increase" } },
                store_delta: { gross_profit_before_marketing_sar: { actual: 300, delta_pct: 10, direction: "increase" } },
                attribution_product_comparison: {
                    campaign_attributed_units: 5,
                    whole_store_product_units: 9,
                    units_unresolved_for_snapchat_decision: 4,
                },
            },
        });

        expect(decision.outcome).toMatchObject({
            status: "successful",
            expected_vs_actual: {
                checks: [{ scope: "campaign", metric: "sales_sar", direction: "increase", met: true }],
            },
            deltas: {
                campaign: { sales_sar: { actual: 140 } },
                account: { orders: { actual: 12 } },
                store: { gross_profit_before_marketing_sar: { actual: 300 } },
            },
            post_attribution: {
                campaign_attributed_units: 5,
                whole_store_product_units: 9,
                units_unresolved_for_snapchat_decision: 4,
            },
        });
    });

    test("uses the governed account and paginated endpoints with abort signals", async () => {
        const signal = new AbortController().signal;
        api.get
            .mockResolvedValueOnce({ data: { accounts: [{ account_id: "acc-1" }] } })
            .mockResolvedValueOnce({ data: { items: [{ id: "change-1" }], total: 1 } });

        await getAdDecisionAccountSummaries({ limitPerAccount: 99, signal });
        await getAdDecisionHistory({ accountId: "acc-1", page: 2, limit: 99, signal });

        expect(api.get).toHaveBeenNthCalledWith(
            1,
            "/integrations-v2/snapchat_ads/decision-ledger/accounts",
            { params: { limit_per_account: 5 }, signal },
        );
        expect(api.get).toHaveBeenNthCalledWith(
            2,
            "/integrations-v2/snapchat_ads/decision-ledger",
            { params: { account_id: "acc-1", page: 2, limit: 5 }, signal },
        );
    });

    test("sends exact read-only diagnostic params and normalizes uncertain evidence", async () => {
        const signal = new AbortController().signal;
        api.get.mockResolvedValueOnce({
            data: {
                read_only: true,
                metric: "contribution_profit_sar",
                headline: { delta_pct: 12.5 },
                likely_contributors: [{
                    decision_id: "decision-7",
                    classification: "association",
                    confidence: "0.61",
                    caveats: ["temporal_association_is_not_causation"],
                }],
                caveats: ["decision_timing_and_direction_support_association_not_causation"],
            },
        });

        const result = await diagnoseAdBusinessChange({
            dateFrom: "2026-08-01",
            dateTo: "2026-08-07",
            metric: "contribution_profit",
            accountId: "acc-7",
            signal,
        });

        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/decision-ledger/diagnose",
            {
                params: {
                    date_from: "2026-08-01",
                    date_to: "2026-08-07",
                    metric: "contribution_profit_sar",
                    account_id: "acc-7",
                },
                signal,
            },
        );
        expect(result).toMatchObject({
            read_only: true,
            metric: "contribution_profit_sar",
            headline: { delta_pct: 12.5 },
            likely_contributors: [{
                decision_id: "decision-7",
                classification: "association",
                confidence: 0.61,
                association_not_causation: true,
            }],
        });
    });

    test("sends suggestions only to the bounded adaptive review and preserves no-write proof", async () => {
        const signal = new AbortController().signal;
        api.post.mockResolvedValueOnce({
            data: {
                mode: "supervised_shadow_learning",
                proposals_created: 0,
                provider_write_reached: false,
                judgments: [{
                    source_mode: "snapchat_adaptive_ai_judgment_v1",
                    provider_write_reached: false,
                    proposal_created: false,
                    judgment: {
                        recommended_action: "observe",
                        entity_type: "campaign",
                        entity_id: "campaign-1",
                        confidence: 0.7,
                        reason_ar: "نحتاج وقتًا أطول.",
                        uncertainties: ["الإسناد غير مكتمل"],
                        safe_to_prepare_proposal: false,
                    },
                }],
            },
        });

        const result = await reviewAdaptiveSnapchat({
            accountId: "acc-1",
            maxEntities: 99,
            userSuggestions: ["  ربما منتصف الشهر  ", ""],
            signal,
        });

        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/decision-ledger/adaptive-review",
            {
                account_id: "acc-1",
                max_entities: 5,
                user_suggestions: ["ربما منتصف الشهر"],
            },
            { signal },
        );
        expect(result).toMatchObject({
            proposals_created: 0,
            provider_write_reached: false,
            judgments: [{
                recommended_action: "observe",
                reason_ar: "نحتاج وقتًا أطول.",
                uncertainties: ["الإسناد غير مكتمل"],
                safe_to_prepare_proposal: false,
                provider_write_reached: false,
                proposal_created: false,
            }],
        });
    });
});
