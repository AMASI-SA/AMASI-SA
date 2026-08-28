import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
        interceptors: {
            request: { use: jest.fn() },
            response: { use: jest.fn() },
        },
    },
    formatApiErrorDetail: jest.fn(() => ""),
}));

jest.mock("../services/snapchatCampaignManagement", () => ({
    approveSnapchatManagementProposal: jest.fn(),
    clearSnapchatManagementPreviewResume: jest.fn(),
    createSnapchatManagementProposal: jest.fn(),
    diagnoseSnapchatManagementPixels: jest.fn(),
    executeSnapchatManagementProposal: jest.fn(),
    getSnapchatEntitySettings: jest.fn(),
    getSnapchatManagementPreviewResume: jest.fn(() => null),
    getSnapchatManagementReadiness: jest.fn(),
    listSnapchatManagementProposals: jest.fn(),
    managementError: (error, fallback) => error?.message || fallback,
    microToNativeAmount: (value) => value == null || value === "" ? null : Number(value) / 1_000_000,
    nativeAmountToMicro: (value) => value == null || value === "" ? null : Math.round(Number(value) * 1_000_000),
    pollSnapchatManagementProposal: jest.fn(),
    reconcileSnapchatManagementProposal: jest.fn(),
    resumeSnapchatManagementProposal: jest.fn(),
    rollbackSnapchatManagementProposal: jest.fn(),
    snapchatBidLabel: (strategy) => strategy === "TARGET_COST" ? "Target Cost" : strategy === "LOWEST_COST_WITH_MAX_BID" ? "Max Bid" : "Bid",
    snapchatFinancialFieldReady: jest.fn(() => false),
    snapchatFinancialSettingsReady: jest.fn(() => false),
}));

jest.mock("../services/mezanProductsV2", () => ({
    listProductsV2: jest.fn(() => Promise.resolve({ items: [] })),
}));

jest.mock("../components/marketing/UnifiedMarketingEntityTable", () => (
    function MockUnifiedMarketingEntityTable({ report, onOpenChildren }) {
        return (
            <div data-testid="mock-unified-table">
                {(report?.rows || []).map((row) => (
                    <button
                        key={row.entity.id}
                        type="button"
                        data-testid={`open-${row.entity.id}`}
                        onClick={() => onOpenChildren?.(row)}
                    >
                        {row.entity.name}
                    </button>
                ))}
            </div>
        );
    }
));

import api from "../lib/api";
import {
    createSnapchatManagementProposal,
    executeSnapchatManagementProposal,
    getSnapchatEntitySettings,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
} from "../services/snapchatCampaignManagement";
import SnapchatV2Page from "./SnapchatV2Page";

describe("SnapchatV2Page read-only load", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        jest.clearAllMocks();
        getSnapchatEntitySettings.mockImplementation(({ entityType }) => Promise.resolve(
            entityType === "campaign"
                ? [{
                    entity_type: "campaign",
                    unified_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                    provider_entity_id: "snap-provider-campaign-afrol",
                    mapping_status: "verified",
                    mapping_verified: true,
                    ad_account_id: "account-1",
                    account_currency: "USD",
                    daily_budget_micro: null,
                    daily_budget_availability: "unsupported_at_provider_level",
                    daily_budget_unavailable_message_ar: "غير متاح من Snapchat على هذا المستوى",
                    ad_squads_daily_budget_micro: 125_000_000,
                    ad_squads_daily_budget_usd: 125,
                    active_ad_squads: 1,
                    ad_squad_bid_strategies: ["TARGET_COST"],
                    quality: {
                        settings_status: "settings_complete",
                        freshness_seconds: 120,
                        freshness_threshold_seconds: 1800,
                        reason: "provider_snapshot_complete",
                    },
                }]
                : [{
                    entity_type: "ad_squad",
                    unified_entity_id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
                    provider_entity_id: "snap-provider-ad-squad-afrol",
                    provider_parent_id: "snap-provider-campaign-afrol",
                    mapping_status: "verified",
                    mapping_verified: true,
                    ad_account_id: "account-1",
                    account_currency: "USD",
                    daily_budget_micro: 125_000_000,
                    daily_budget_usd: 125,
                    bid_micro: 15_000_000,
                    bid_usd: 15,
                    bid_strategy: "TARGET_COST",
                    optimization_goal: "PIXEL_PURCHASE",
                    billing_event: "IMPRESSION",
                    conversion_window: { swipe_window: "28_DAY", view_window: "1_DAY" },
                    status: "ACTIVE",
                    quality: {
                        settings_status: "settings_complete",
                        freshness_seconds: 120,
                        freshness_threshold_seconds: 1800,
                        reason: "provider_snapshot_complete",
                    },
                }],
        ));
        getSnapchatManagementReadiness.mockResolvedValue({
            proposal_enabled: true,
            execution_enabled: false,
            activation_enabled: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                currency: "USD",
                management_allowed: true,
                creative_allowed: true,
                pixels: [],
            }],
        });
        listSnapchatManagementProposals.mockResolvedValue([]);
        api.get.mockImplementation((url) => {
            if (url.endsWith("/status")) {
                return Promise.resolve({
                    data: {
                        selected_account: {
                            ad_account_id: "account-1",
                            display_name: "AMASI",
                            currency: "USD",
                            timezone: "America/Los_Angeles",
                        },
                    },
                });
            }
            if (url.endsWith("/report")) {
                return Promise.resolve({
                    data: {
                        currency: "USD",
                        totals: {
                            delivery: { spend: { amount: 0, currency: "USD" } },
                            platform_outcomes: {},
                        },
                    },
                });
            }
            if (url.endsWith("/hourly")) {
                return Promise.resolve({ data: { hours: [], totals: {} } });
            }
            if (url.endsWith("/campaigns")) {
                return Promise.resolve({
                    data: {
                        unified: {
                            contract_version: "2",
                            entity_level: "campaign",
                            rows: [{
                                entity: {
                                    level: "campaign",
                                    provider_level: "campaign",
                                    id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                                    name: "افرول الوطني",
                                    status: "ACTIVE",
                                },
                                quality: {
                                    sync_status: "complete",
                                    coverage_status: "complete",
                                    source_fact_count: 4,
                                },
                            }],
                            totals: null,
                        },
                        salla: { summary: {} },
                    },
                });
            }
            if (url.endsWith("/ad-squads")) {
                return Promise.resolve({
                    data: {
                        unified: {
                            contract_version: "2",
                            entity_level: "ad_group",
                            rows: [{
                                entity: {
                                    level: "ad_group",
                                    provider_level: "ad_squad",
                                    id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
                                    campaign_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                                    name: "Ad Squad افرول الوطني",
                                    status: "ACTIVE",
                                },
                                quality: {
                                    sync_status: "complete",
                                    coverage_status: "complete",
                                    source_fact_count: 4,
                                },
                            }],
                            totals: null,
                        },
                    },
                });
            }
            if (url.endsWith("/unified-readiness")) {
                return Promise.resolve({ data: { ready: true, reasons: [] } });
            }
            throw new Error(`Unexpected GET: ${url}`);
        });
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("render and opening management issue no POST, proposal creation, or execution", async () => {
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(getSnapchatEntitySettings).toHaveBeenCalledWith(expect.objectContaining({
            entityType: "campaign",
        }));
        expect(container.textContent).toContain("da5049b7-5417-4be9-a596-20a74f9fd54c");
        expect(container.textContent).toContain("snap-provider-campaign-afrol");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();

        await act(async () => {
            container.querySelector('[data-testid="open-da5049b7-5417-4be9-a596-20a74f9fd54c"]').click();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(getSnapchatEntitySettings).toHaveBeenCalledWith(expect.objectContaining({
            entityType: "ad_squad",
            parentUnifiedId: "da5049b7-5417-4be9-a596-20a74f9fd54c",
        }));
        expect(container.textContent).toContain("7c0f5bfa-3f59-437b-bb89-1c70b11d0526");
        expect(container.textContent).toContain("snap-provider-ad-squad-afrol");

        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(getSnapchatManagementReadiness).toHaveBeenCalledTimes(1);
        expect(listSnapchatManagementProposals).toHaveBeenCalledTimes(1);
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("fails closed when entity settings belong to another ad account", async () => {
        getSnapchatEntitySettings.mockResolvedValueOnce([{
            entity_type: "campaign",
            unified_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
            provider_entity_id: "snap-provider-campaign-afrol",
            mapping_status: "verified",
            mapping_verified: true,
            ad_account_id: "account-other",
            account_currency: "USD",
            daily_budget_micro: 99_000_000,
            quality: {
                settings_status: "settings_complete",
                freshness_seconds: 10,
                freshness_threshold_seconds: 1800,
                financial_controls_allowed: true,
            },
        }]);
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(container.textContent).toContain("settings_sync_failed");
        expect(container.textContent).toContain("فشل إثبات ارتباط إعدادات الكيان بالحساب الإعلاني المحدد");
        expect(container.textContent).not.toContain("99.00 USD");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });
});
