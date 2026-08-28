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
                            mapping_verified: true,
                            account_currency: "USD",
                            campaign_daily_budget_supported: false,
                            daily_budget_availability: "unsupported_at_provider_level",
                            daily_budget_unavailable_message_ar: "غير متاح من Snapchat على هذا المستوى",
                            daily_budget_micro: null,
                            ad_squads_daily_budget_micro: 75_000_000,
                            ad_squads_daily_budget_usd: 75,
                            active_ad_squads: 3,
                            ad_squad_bid_strategies: ["AUTO_BID", "TARGET_COST"],
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
        expect(container.textContent).not.toContain(">ACTIVE<");
    });

    test("separates performance completeness from settings state", () => {
        expect(snapchatPerformanceStatus(row("campaign", "one", "complete"))).toBe("performance_complete");
        expect(snapchatPerformanceStatus(row("campaign", "two", "missing"))).toBe("performance_no_facts");
        expect(snapchatPerformanceStatus({
            quality: { sync_status: "partial", source_fact_count: 4 },
        })).toBe("performance_partial");
    });
});
