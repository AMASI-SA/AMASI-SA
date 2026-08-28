import React, { act } from "react";
import { createRoot } from "react-dom/client";

import SnapchatEntitySettingsTable, {
    SNAPCHAT_CAMPAIGN_BUDGET_UNSUPPORTED,
    SNAPCHAT_SETTINGS_UNAVAILABLE,
    snapchatPerformanceStatus,
} from "./SnapchatEntitySettingsTable";

function row(level, id, coverageStatus = "complete") {
    return {
        entity: {
            level,
            id,
            name: `Entity ${id}`,
            status: "ACTIVE",
        },
        quality: {
            coverage_status: coverageStatus,
            sync_status: coverageStatus === "complete" ? "complete" : "no_facts",
        },
    };
}

describe("SnapchatEntitySettingsTable", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("keeps campaign budget separate from child squad budgets and exposes freshness proof", async () => {
        const campaign = row("campaign", "unified-campaign-1");
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [campaign] }}
                    settingsByEntityId={{
                        "unified-campaign-1": {
                            unified_entity_id: "unified-campaign-1",
                            provider_entity_id: "provider-campaign-9",
                            ad_account_id: "account-1",
                            mapping_verified: true,
                            account_currency: "USD",
                            campaign_daily_budget_supported: false,
                            daily_budget_availability: "unsupported_at_provider_level",
                            daily_budget_unavailable_message_ar: "غير متاح من Snapchat على هذا المستوى",
                            daily_budget_micro: null,
                            ad_squads_daily_budget_micro: 75_000_000,
                            ad_squads_daily_budget_usd: 75,
                            active_ad_squads: 3,
                            active_ad_squads_availability: "available",
                            ad_squad_bid_strategies: ["AUTO_BID", "TARGET_COST"],
                            ad_squad_bid_strategies_availability: "available",
                            status: "ACTIVE",
                            settings_synced_at: "2026-08-28T10:00:00Z",
                            provider_updated_at: "2026-08-28T09:55:00Z",
                            quality: {
                                settings_status: "settings_complete",
                                freshness_seconds: 120,
                                freshness_threshold_seconds: 1800,
                                reason: "provider_snapshot_complete",
                            },
                        },
                    }}
                />,
            );
        });

        expect(SNAPCHAT_CAMPAIGN_BUDGET_UNSUPPORTED).toBe("غير متاح من Snapchat على هذا المستوى");
        expect(container.textContent).toContain("غير متاح من Snapchat على هذا المستوى");
        expect(container.textContent).toContain("75.00 USD");
        expect(container.textContent).toContain("3");
        expect(container.textContent).toContain("AUTO_BID");
        expect(container.textContent).toContain("TARGET_COST");
        expect(container.textContent).toContain("120 ثانية");
        expect(container.textContent).toContain("1,800 ثانية");
        expect(container.textContent).toContain("settings_synced_at");
        expect(container.textContent).toContain("provider_updated_at");
        expect(container.textContent).toContain("unified-campaign-1");
        expect(container.textContent).toContain("provider-campaign-9");
        expect(container.textContent).toContain("account-1");
    });

    test("fails campaign aggregate counts and strategies closed without coverage proof", async () => {
        const campaignId = "campaign-incomplete-catalog";
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [row("campaign", campaignId)] }}
                    settingsByEntityId={{
                        [campaignId]: {
                            unified_entity_id: campaignId,
                            provider_entity_id: campaignId,
                            account_currency: "USD",
                            daily_budget_availability: "unsupported_at_provider_level",
                            active_ad_squads: 0,
                            active_ad_squads_availability: "child_catalog_account_count_mismatch",
                            ad_squad_bid_strategies: [],
                            ad_squad_bid_strategies_availability: "child_catalog_account_count_mismatch",
                            quality: { settings_status: "settings_complete" },
                        },
                    }}
                />,
            );
        });

        expect(container.querySelector('[data-testid="snapchat-settings-active-ad-squads"]').textContent)
            .toContain(SNAPCHAT_SETTINGS_UNAVAILABLE);
        expect(container.querySelector('[data-testid="snapchat-settings-ad-squad-bid-strategies"]').textContent)
            .toContain(SNAPCHAT_SETTINGS_UNAVAILABLE);
    });

    test("preserves proven zero and empty strategies for a complete zero-child partition", async () => {
        const campaignId = "campaign-complete-zero-children";
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [row("campaign", campaignId)] }}
                    settingsByEntityId={{
                        [campaignId]: {
                            unified_entity_id: campaignId,
                            provider_entity_id: campaignId,
                            account_currency: "USD",
                            daily_budget_availability: "unsupported_at_provider_level",
                            active_ad_squads: 0,
                            active_ad_squads_availability: "available",
                            ad_squad_bid_strategies: [],
                            ad_squad_bid_strategies_availability: "available",
                            quality: { settings_status: "settings_complete" },
                        },
                    }}
                />,
            );
        });

        const active = container.querySelector('[data-testid="snapchat-settings-active-ad-squads"]');
        const strategies = container.querySelector('[data-testid="snapchat-settings-ad-squad-bid-strategies"]');
        expect(active.textContent).toContain("0");
        expect(active.textContent).not.toContain(SNAPCHAT_SETTINGS_UNAVAILABLE);
        expect(strategies.textContent).toContain("لا توجد (0 Ad Squads)");
        expect(strategies.textContent).not.toContain(SNAPCHAT_SETTINGS_UNAVAILABLE);
    });

    test("documents the native Snapchat V2 identity contract while showing both equal IDs", async () => {
        const providerId = "7c0f5bfa-3f59-437b-bb89-1c70b11d0526";
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [row("ad_group", providerId)] }}
                    settingsByEntityId={{
                        [providerId]: {
                            unified_entity_id: providerId,
                            provider_entity_id: providerId,
                            ad_account_id: "account-1",
                            mapping_status: "verified",
                            mapping_verified: true,
                            identity_contract: {
                                name: "snapchat_v2_provider_id_is_unified_id_v1",
                                requires_equal: true,
                                ids_equal: true,
                                unified_id_source: "mezan_snapchat_entities_v2.external_id",
                                provider_id_source: "mezan_snapchat_entities_v2.provider_snapshot.id",
                            },
                            account_currency: "USD",
                            daily_budget_micro: 50_000_000,
                            bid_micro: 7_500_000,
                            bid_strategy: "TARGET_COST",
                            quality: {
                                settings_status: "settings_complete",
                                freshness_seconds: 120,
                                freshness_threshold_seconds: 1800,
                            },
                        },
                    }}
                />,
            );
        });

        const diagnostic = container.textContent;
        expect(diagnostic).toContain("Unified ID");
        expect(diagnostic).toContain("Snapchat provider ID");
        const labels = Array.from(container.querySelectorAll("span"));
        expect(labels.find((item) => item.textContent === "Unified ID")?.parentElement?.textContent)
            .toContain(providerId);
        expect(labels.find((item) => item.textContent === "Snapchat provider ID")?.parentElement?.textContent)
            .toContain(providerId);
        expect(diagnostic).toContain("snapchat_v2_provider_id_is_unified_id_v1");
        expect(diagnostic).toContain("Unified ID == provider ID");
        expect(diagnostic).toContain("true");
    });

    test("labels bid by strategy and never calls a max bid Target Cost", async () => {
        const adSquad = row("ad_group", "unified-squad-1", "missing");
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [adSquad] }}
                    settingsByEntityId={{
                        "unified-squad-1": {
                            unified_entity_id: "unified-squad-1",
                            provider_entity_id: "provider-squad-8",
                            account_currency: "USD",
                            daily_budget_micro: 50_000_000,
                            daily_budget_usd: 50,
                            bid_micro: 7_500_000,
                            bid_usd: 7.5,
                            bid_strategy: "LOWEST_COST_WITH_MAX_BID",
                            optimization_goal: "PIXEL_PURCHASE",
                            billing_event: "IMPRESSION",
                            conversion_window: "SWIPE_7DAY",
                            status: "ACTIVE",
                            quality: {
                                settings_status: "settings_complete",
                                freshness_seconds: 120,
                                freshness_threshold_seconds: 1800,
                                reason: "provider_snapshot_complete",
                            },
                        },
                    }}
                />,
            );
        });

        const bidLabel = container.querySelector('[data-testid="snapchat-settings-bid-label"]');
        expect(bidLabel.textContent).toContain("Max Bid");
        expect(bidLabel.textContent).not.toContain("Target Cost");
        expect(container.textContent).toContain("performance_no_facts");
        expect(container.textContent).toContain("settings_complete");
        expect(container.textContent).toContain("7.50 USD");
    });

    test("missing provider values remain unavailable instead of zero", async () => {
        const adSquad = row("ad_group", "unified-squad-missing");
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [adSquad] }}
                    settingsByEntityId={{
                        "unified-squad-missing": {
                            unified_entity_id: "unified-squad-missing",
                            provider_entity_id: null,
                            account_currency: "USD",
                            daily_budget_micro: null,
                            bid_micro: null,
                            quality: {
                                settings_status: "settings_sync_failed",
                                freshness_seconds: null,
                                reason: "provider_read_failed",
                            },
                        },
                    }}
                />,
            );
        });
        expect(container.textContent).toContain(SNAPCHAT_SETTINGS_UNAVAILABLE);
        expect(container.textContent).not.toContain("0.00 USD");
        expect(container.textContent).toContain("settings_sync_failed");
    });

    test("stale snapshots do not expose old provider values as current", async () => {
        const campaign = row("campaign", "stale-campaign");
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [campaign] }}
                    settingsByEntityId={{
                        "stale-campaign": {
                            unified_entity_id: "stale-campaign",
                            provider_entity_id: "provider-stale-campaign",
                            account_currency: "USD",
                            daily_budget_micro: 25_000_000,
                            status: "ACTIVE",
                            quality: {
                                settings_status: "settings_stale",
                                freshness_seconds: 4000,
                                freshness_threshold_seconds: 1800,
                                reason: "older_than_freshness_limit",
                            },
                        },
                    }}
                />,
            );
        });
        expect(container.textContent).toContain("settings_stale");
        expect(container.textContent).toContain(SNAPCHAT_SETTINGS_UNAVAILABLE);
        expect(container.textContent).not.toContain("25.00 USD");
        expect(container.textContent).not.toContain("ACTIVE");
    });

    test("separates performance completeness from settings state", () => {
        expect(snapchatPerformanceStatus(row("campaign", "one", "complete"))).toBe("performance_complete");
        expect(snapchatPerformanceStatus(row("campaign", "two", "missing"))).toBe("performance_no_facts");
        expect(snapchatPerformanceStatus({
            quality: { sync_status: "partial", source_fact_count: 4 },
        })).toBe("performance_partial");
        expect(snapchatPerformanceStatus({
            quality: { sync_status: "failed", source_fact_count: 0 },
        })).toBe("performance_sync_failed");
        expect(snapchatPerformanceStatus({
            quality: { performance_status: "performance_sync_failed" },
        })).toBe("performance_sync_failed");
    });

    test("does not render bid_micro as an amount for AUTO_BID", async () => {
        const adSquad = row("ad_group", "auto-bid-squad");
        await act(async () => {
            root.render(
                <SnapchatEntitySettingsTable
                    report={{ rows: [adSquad] }}
                    settingsByEntityId={{
                        "auto-bid-squad": {
                            unified_entity_id: "auto-bid-squad",
                            provider_entity_id: "provider-auto-bid-squad",
                            ad_account_id: "account-1",
                            account_currency: "USD",
                            bid_micro: 99_000_000,
                            bid_strategy: "AUTO_BID",
                            quality: { settings_status: "settings_complete" },
                        },
                    }}
                />,
            );
        });
        const bid = container.querySelector('[data-testid="snapchat-settings-bid-label"]');
        expect(bid.textContent).toContain("غير مستخدم مع AUTO_BID");
        expect(container.textContent).not.toContain("99.00 USD");
        expect(container.textContent).not.toContain("Target Cost");
    });
});
