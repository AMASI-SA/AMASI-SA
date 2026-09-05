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
import SnapchatV2Page, { validateTargetedSettings } from "./SnapchatV2Page";

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function verifiedTargetedSettings(unifiedEntityId, overrides = {}) {
    return {
        entity_type: "campaign",
        unified_entity_id: unifiedEntityId,
        provider_entity_id: unifiedEntityId,
        mapping_status: "verified",
        mapping_verified: true,
        ad_account_id: "account-1",
        account_currency: "USD",
        daily_budget_micro: null,
        daily_budget_availability: "unsupported_at_provider_level",
        identity_contract: {
            name: "snapchat_v2_provider_id_is_unified_id_v1",
            ids_equal: true,
        },
        quality: {
            settings_status: "settings_complete",
            freshness_seconds: 10,
            freshness_threshold_seconds: 1800,
            financial_controls_allowed: true,
            reason: "targeted-settings-current",
        },
        ...overrides,
    };
}

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
            if (url.endsWith("/ads")) {
                return Promise.resolve({
                    data: {
                        unified: {
                            contract_version: "2",
                            entity_level: "ad",
                            rows: [{
                                entity: {
                                    level: "ad",
                                    provider_level: "ad",
                                    id: "snap-provider-ad-afrol",
                                    campaign_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                                    ad_group_id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
                                    name: "Ad افرول الوطني",
                                    status: "ACTIVE",
                                },
                                quality: {
                                    sync_status: "complete",
                                    coverage_status: "complete",
                                    source_fact_count: 4,
                                },
                            }],
                            page: 1,
                            page_size: 25,
                            total: 1,
                            filtered_total: 1,
                            pages: 1,
                            filters: {},
                            sort: {},
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
            unifiedEntityIds: ["da5049b7-5417-4be9-a596-20a74f9fd54c"],
            limit: 1,
        }));
        expect(api.get.mock.calls.some(([url]) => url.endsWith("/ad-squads"))).toBe(false);
        expect(api.get.mock.calls.some(([url]) => url.endsWith("/ads"))).toBe(false);
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
        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat-v2/ad-squads",
            { params: expect.objectContaining({
                campaign_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                page: 1,
                page_size: 25,
            }) },
        );
        expect(container.textContent).toContain("7c0f5bfa-3f59-437b-bb89-1c70b11d0526");
        expect(container.textContent).toContain("snap-provider-ad-squad-afrol");

        await act(async () => {
            container.querySelector(
                '[data-testid="manage-7c0f5bfa-3f59-437b-bb89-1c70b11d0526"]',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(container.querySelector('[data-testid="snapchat-management-drawer"]')).not.toBeNull();
        expect(getSnapchatEntitySettings.mock.calls.filter(([input]) => (
            input.unifiedEntityId === "7c0f5bfa-3f59-437b-bb89-1c70b11d0526"
        ))).toHaveLength(1);

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

    test("finishes account readiness after navigating to a child settings context without writes", async () => {
        const readinessResponse = deferred();
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => (
            url.endsWith("/unified-readiness")
                ? readinessResponse.promise
                : defaultGet(url, config)
        ));

        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(container.querySelector('[data-testid="snapchat-unified-readiness"]').textContent).toContain("جارٍ التحقق");

        await act(async () => {
            container.querySelector('[data-testid="open-da5049b7-5417-4be9-a596-20a74f9fd54c"]').click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            readinessResponse.resolve({ data: { ready: true, reasons: [] } });
            await readinessResponse.promise;
            await Promise.resolve();
        });

        expect(container.querySelector('[data-testid="snapchat-unified-readiness"]').textContent).toContain("جاهز");
        expect(container.querySelector('[data-testid="snapchat-unified-readiness"]').textContent).not.toContain("جارٍ التحقق");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("keeps the latest account settings when an older account request resolves last without writes", async () => {
        const accountASettings = deferred();
        const accountBSettings = deferred();
        getSnapchatEntitySettings
            .mockImplementationOnce(() => accountASettings.promise)
            .mockImplementationOnce(() => accountBSettings.promise);
        const defaultGet = api.get.getMockImplementation();
        let statusRequestCount = 0;
        api.get.mockImplementation((url, config) => {
            if (url.endsWith("/status")) {
                statusRequestCount += 1;
                const accountSuffix = statusRequestCount === 1 ? "A" : "B";
                return Promise.resolve({
                    data: {
                        selected_account: {
                            ad_account_id: `account-${accountSuffix}`,
                            display_name: `AMASI ${accountSuffix}`,
                            currency: "USD",
                            timezone: "America/Los_Angeles",
                        },
                    },
                });
            }
            return defaultGet(url, config);
        });

        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(getSnapchatEntitySettings).toHaveBeenCalledTimes(1);

        await act(async () => {
            container.querySelector('button[type="submit"]').click();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(getSnapchatEntitySettings).toHaveBeenCalledTimes(2);

        await act(async () => {
            accountBSettings.resolve([{
                entity_type: "campaign",
                unified_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                provider_entity_id: "provider-campaign-B",
                mapping_status: "verified",
                mapping_verified: true,
                ad_account_id: "account-B",
                account_currency: "USD",
                daily_budget_micro: null,
                daily_budget_availability: "unsupported_at_provider_level",
                daily_budget_unavailable_message_ar: "غير متاح من Snapchat على هذا المستوى",
                quality: {
                    settings_status: "settings_complete",
                    freshness_seconds: 20,
                    freshness_threshold_seconds: 1800,
                    reason: "settings-from-account-B",
                },
            }]);
            await accountBSettings.promise;
            await Promise.resolve();
        });
        expect(container.textContent).toContain("provider-campaign-B");
        expect(container.textContent).toContain("settings-from-account-B");
        expect(container.textContent).toContain("account-B");

        await act(async () => {
            accountASettings.resolve([{
                entity_type: "campaign",
                unified_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                provider_entity_id: "provider-campaign-A",
                mapping_status: "verified",
                mapping_verified: true,
                ad_account_id: "account-A",
                account_currency: "USD",
                daily_budget_micro: null,
                daily_budget_availability: "unsupported_at_provider_level",
                daily_budget_unavailable_message_ar: "غير متاح من Snapchat على هذا المستوى",
                quality: {
                    settings_status: "settings_complete",
                    freshness_seconds: 10,
                    freshness_threshold_seconds: 1800,
                    reason: "settings-from-account-A",
                },
            }]);
            await accountASettings.promise;
            await Promise.resolve();
        });

        expect(container.textContent).toContain("provider-campaign-B");
        expect(container.textContent).toContain("settings-from-account-B");
        expect(container.textContent).not.toContain("provider-campaign-A");
        expect(container.textContent).not.toContain("settings-from-account-A");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("opening the hierarchy reads only the selected campaign and Ad Squad children", async () => {
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(api.get.mock.calls.some(([url]) => url.endsWith("/ad-squads"))).toBe(false);
        expect(api.get.mock.calls.some(([url]) => url.endsWith("/ads"))).toBe(false);

        await act(async () => {
            container.querySelector('[data-testid="open-da5049b7-5417-4be9-a596-20a74f9fd54c"]').click();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(api.get.mock.calls.filter(([url]) => url.endsWith("/ad-squads"))).toHaveLength(1);
        expect(api.get.mock.calls.some(([url]) => url.endsWith("/ads"))).toBe(false);

        await act(async () => {
            container.querySelector('[data-testid="open-7c0f5bfa-3f59-437b-bb89-1c70b11d0526"]').click();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat-v2/ads",
            { params: expect.objectContaining({
                campaign_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                ad_squad_id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
                page: 1,
                page_size: 25,
            }) },
        );
        expect(container.textContent).toContain("snap-provider-ad-afrol");
        expect(api.post).not.toHaveBeenCalled();
    });

    test("a campaign beyond the old 500-row cap gets exactly one targeted settings read", async () => {
        const campaignId = "campaign-00501";
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => {
            if (url.endsWith("/campaigns")) {
                return Promise.resolve({ data: { unified: {
                    contract_version: "2",
                    entity_level: "campaign",
                    rows: [{
                        entity: { level: "campaign", provider_level: "campaign", id: campaignId, name: "Campaign 501", status: "ACTIVE" },
                        quality: { sync_status: "complete", coverage_status: "complete" },
                    }],
                    page: 21,
                    page_size: 25,
                    total: 5_000,
                    filtered_total: 5_000,
                    pages: 200,
                    filters: {},
                    sort: {},
                }, salla: { summary: {} } } });
            }
            return defaultGet(url, config);
        });
        getSnapchatEntitySettings.mockImplementation((input) => Promise.resolve(
            input.unifiedEntityId ? [verifiedTargetedSettings(campaignId)] : [],
        ));

        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(`[data-testid="manage-${campaignId}"]`).click();
            await Promise.resolve();
            await Promise.resolve();
        });

        const targetedCalls = getSnapchatEntitySettings.mock.calls.filter(
            ([input]) => input.unifiedEntityId === campaignId,
        );
        expect(targetedCalls).toHaveLength(1);
        expect(targetedCalls[0][0]).toEqual(expect.objectContaining({
            entityType: "campaign",
            unifiedEntityId: campaignId,
            limit: 1,
        }));
        expect(container.querySelector('[data-testid="snapchat-management-current-settings"]').textContent).toContain("targeted-settings-current");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("a late visible-page settings response cannot overwrite targeted settings", async () => {
        const campaignId = "da5049b7-5417-4be9-a596-20a74f9fd54c";
        const bulk = deferred();
        getSnapchatEntitySettings
            .mockImplementationOnce(() => bulk.promise)
            .mockImplementationOnce(() => Promise.resolve([
                verifiedTargetedSettings(campaignId, {
                    quality: {
                        settings_status: "settings_complete",
                        freshness_seconds: 8,
                        freshness_threshold_seconds: 1800,
                        financial_controls_allowed: true,
                        reason: "targeted-settings-wins",
                    },
                }),
            ]));

        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(`[data-testid="manage-${campaignId}"]`).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(container.textContent).toContain("targeted-settings-wins");

        await act(async () => {
            bulk.resolve([verifiedTargetedSettings(campaignId, {
                quality: {
                    settings_status: "settings_complete",
                    freshness_seconds: 999,
                    freshness_threshold_seconds: 1800,
                    financial_controls_allowed: true,
                    reason: "late-bulk-must-not-win",
                },
            })]);
            await bulk.promise;
            await Promise.resolve();
        });

        expect(container.textContent).toContain("targeted-settings-wins");
        expect(container.textContent).not.toContain("late-bulk-must-not-win");
    });

    test("a late targeted response for campaign A is ignored after campaign B is selected", async () => {
        const campaignA = "campaign-A";
        const campaignB = "campaign-B";
        const targetA = deferred();
        const targetB = deferred();
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => {
            if (url.endsWith("/campaigns")) {
                return Promise.resolve({ data: { unified: {
                    contract_version: "2",
                    entity_level: "campaign",
                    rows: [campaignA, campaignB].map((id) => ({
                        entity: { level: "campaign", provider_level: "campaign", id, name: id, status: "ACTIVE" },
                        quality: { sync_status: "complete", coverage_status: "complete" },
                    })),
                    page: 1,
                    page_size: 25,
                    total: 2,
                    filtered_total: 2,
                    pages: 1,
                    filters: {},
                    sort: {},
                }, salla: { summary: {} } } });
            }
            return defaultGet(url, config);
        });
        getSnapchatEntitySettings
            .mockImplementationOnce(() => Promise.resolve([]))
            .mockImplementationOnce(() => targetA.promise)
            .mockImplementationOnce(() => targetB.promise);

        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(`[data-testid="manage-${campaignA}"]`).click();
            container.querySelector(`[data-testid="manage-${campaignB}"]`).click();
            targetB.resolve([verifiedTargetedSettings(campaignB, {
                quality: { settings_status: "settings_complete", freshness_seconds: 7, freshness_threshold_seconds: 1800, financial_controls_allowed: true, reason: "campaign-B-current" },
            })]);
            await targetB.promise;
            await Promise.resolve();
        });
        expect(container.textContent).toContain("campaign-B-current");

        await act(async () => {
            targetA.resolve([verifiedTargetedSettings(campaignA, {
                quality: { settings_status: "settings_complete", freshness_seconds: 6, freshness_threshold_seconds: 1800, financial_controls_allowed: true, reason: "campaign-A-stale" },
            })]);
            await targetA.promise;
            await Promise.resolve();
        });

        expect(container.textContent).toContain("campaign-B-current");
        expect(container.textContent).not.toContain("campaign-A-stale");
    });

    test("a superseded server-filter response cannot replace the latest entity page", async () => {
        jest.useFakeTimers();
        const oldPage = deferred();
        const newPage = deferred();
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => {
            if (url.endsWith("/campaigns") && config?.params?.search === "old") return oldPage.promise;
            if (url.endsWith("/campaigns") && config?.params?.search === "new") return newPage.promise;
            return defaultGet(url, config);
        });
        const response = (id, search) => ({ data: {
            unified: {
                contract_version: "2",
                entity_level: "campaign",
                rows: [{
                    entity: { level: "campaign", provider_level: "campaign", id, name: id, status: "ACTIVE" },
                    quality: { sync_status: "complete", coverage_status: "complete" },
                }],
                page: 1,
                page_size: 25,
                total: 5_000,
                filtered_total: 1,
                pages: 1,
                filters: { search, active_only: false },
                sort: { by: "default", direction: "desc" },
            },
            salla: { summary: {} },
        } });
        try {
            await act(async () => {
                root.render(<SnapchatV2Page />);
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();
                jest.advanceTimersByTime(300);
            });
            const input = container.querySelector('[data-testid="snapchat-server-search"]');
            const setInput = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype,
                "value",
            ).set;

            await act(async () => {
                setInput.call(input, "old");
                input.dispatchEvent(new Event("input", { bubbles: true }));
                jest.advanceTimersByTime(300);
                await Promise.resolve();
            });
            await act(async () => {
                setInput.call(input, "new");
                input.dispatchEvent(new Event("input", { bubbles: true }));
                jest.advanceTimersByTime(300);
                await Promise.resolve();
            });

            await act(async () => {
                newPage.resolve(response("campaign-new", "new"));
                await newPage.promise;
                await Promise.resolve();
            });
            expect(container.textContent).toContain("campaign-new");

            await act(async () => {
                oldPage.resolve(response("campaign-old", "old"));
                await oldPage.promise;
                await Promise.resolve();
            });
            expect(container.textContent).toContain("campaign-new");
            expect(container.textContent).not.toContain("campaign-old");
        } finally {
            jest.useRealTimers();
        }
    });

    test("targeted identity validation fails closed for wrong entity, account, or parent", () => {
        const base = verifiedTargetedSettings("squad-1", {
            entity_type: "ad_squad",
            provider_parent_id: "campaign-1",
        });
        const cases = [
            { ...base, entity_type: "campaign" },
            { ...base, ad_account_id: "account-other" },
            { ...base, provider_parent_id: "campaign-other" },
        ];

        cases.forEach((item) => {
            const result = validateTargetedSettings([item], {
                entityType: "ad_squad",
                unifiedEntityId: "squad-1",
                parentUnifiedId: "campaign-1",
                accountId: "account-1",
            });
            expect(result.quality.settings_status).toBe("settings_sync_failed");
            expect(result.quality.financial_controls_allowed).toBe(false);
        });
    });
});
