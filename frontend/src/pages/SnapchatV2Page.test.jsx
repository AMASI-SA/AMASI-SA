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
    function MockUnifiedMarketingEntityTable({ report, onOpenChildren, onManageEntity }) {
        return (
            <div data-testid="mock-unified-table">
                {(report?.rows || []).map((row) => (
                    <div key={row.entity.id}>
                        <button
                            type="button"
                            data-testid={`open-${row.entity.id}`}
                            onClick={() => onOpenChildren?.(row)}
                        >
                            {row.entity.name}
                        </button>
                        <button
                            type="button"
                            data-testid={`manage-${row.entity.id}`}
                            onClick={() => onManageEntity?.(row)}
                        >
                            تعديل / حالة
                        </button>
                    </div>
                ))}
            </div>
        );
    }
));

jest.mock("../components/marketing/SnapchatEntitySettingsTable", () => (
    function MockSnapchatEntitySettingsTable({ settingsByEntityId = {} }) {
        const entries = Object.entries(settingsByEntityId);
        const sampled = entries.length <= 8
            ? entries
            : [...entries.slice(0, 4), ...entries.slice(-4)];
        return (
            <div data-testid="mock-snapchat-settings-table">
                {sampled.map(([id, settings]) => (
                    <div key={id}>
                        {id} · {settings?.provider_entity_id || "—"} · {settings?.ad_account_id || "—"} · {settings?.quality?.settings_status || "—"} · {settings?.quality?.reason || "—"}
                    </div>
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

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function identityContract() {
    return {
        name: "snapchat_v2_provider_id_is_unified_id_v1",
        requires_equal: true,
        ids_equal: true,
    };
}

function campaignRow(id, name = id) {
    return {
        entity: {
            level: "campaign",
            provider_level: "campaign",
            id,
            name,
            status: "ACTIVE",
        },
        quality: {
            sync_status: "complete",
            coverage_status: "complete",
            source_fact_count: 1,
        },
    };
}

function completeCampaignSettings(id, overrides = {}) {
    return {
        entity_type: "campaign",
        unified_entity_id: id,
        provider_entity_id: id,
        ad_account_id: "account-1",
        account_currency: "USD",
        mapping_status: "verified",
        mapping_verified: true,
        identity_contract: identityContract(),
        daily_budget_micro: 50_000_000,
        daily_budget_usd: 50,
        quality: {
            settings_status: "settings_complete",
            freshness_seconds: 30,
            freshness_threshold_seconds: 1800,
            reason: "targeted-settings-complete",
            financial_controls_allowed: true,
            financial_field_controls: {
                daily_budget: { allowed: true },
            },
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
        Element.prototype.scrollIntoView = jest.fn();
        jest.clearAllMocks();
        getSnapchatEntitySettings.mockImplementation(({ entityType }) => Promise.resolve(
            entityType === "campaign"
                ? [{
                    entity_type: "campaign",
                    unified_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                    provider_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                    mapping_status: "verified",
                    mapping_verified: true,
                    identity_contract: identityContract(),
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
                    provider_entity_id: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
                    provider_parent_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                    mapping_status: "verified",
                    mapping_verified: true,
                    identity_contract: identityContract(),
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
        expect(container.textContent).toContain("da5049b7-5417-4be9-a596-20a74f9fd54c");
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
        expect(container.textContent).toContain("7c0f5bfa-3f59-437b-bb89-1c70b11d0526");

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

    test("opens failed entity settings in diagnostic-only mode without writes", async () => {
        getSnapchatEntitySettings.mockResolvedValue([{
            entity_type: "campaign",
            unified_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
            provider_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
            mapping_status: "verified",
            mapping_verified: true,
            identity_contract: identityContract(),
            ad_account_id: "account-1",
            account_currency: "USD",
            daily_budget_micro: 40_000_000,
            status: "ACTIVE",
            quality: {
                settings_status: "settings_sync_failed",
                freshness_seconds: null,
                freshness_threshold_seconds: 1800,
                reason: "campaign_provider_snapshot_missing",
                financial_controls_allowed: false,
                financial_field_controls: {},
            },
        }]);
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });

        const panelToggle = container.querySelector(
            '[data-testid="snapchat-campaign-management-panel"] > button',
        );
        expect(panelToggle.getAttribute("aria-expanded")).toBe("false");

        await act(async () => {
            container.querySelector(
                '[data-testid="manage-da5049b7-5417-4be9-a596-20a74f9fd54c"]',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(container.querySelector(
            '[data-testid="snapchat-campaign-management-panel"] > button',
        ).getAttribute("aria-expanded")).toBe("true");
        const currentSettings = container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        );
        expect(currentSettings.textContent).toContain("settings_sync_failed");
        expect(currentSettings.textContent).toContain("campaign_provider_snapshot_missing");
        expect(currentSettings.textContent).toContain("da5049b7-5417-4be9-a596-20a74f9fd54c");
        expect(container.querySelector('[data-testid="snapchat-management-current-daily-budget"]').textContent)
            .toContain("غير متاح — فشل جلب الإعدادات");
        expect(container.querySelector('[data-testid="snapchat-management-diagnostic-values"]').textContent)
            .toContain("40.00 USD");
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(true);

        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
        });

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

    test("loads campaign 1012 with one targeted GET and keeps it over a late bulk fallback", async () => {
        const targetId = "campaign-after-first-500";
        const rows = Array.from({ length: 1011 }, (_, index) => (
            campaignRow(`campaign-${String(index + 1).padStart(4, "0")}`)
        ));
        rows.push(campaignRow(targetId, "الحملة بعد أول 500"));
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => {
            if (url.endsWith("/campaigns")) {
                return Promise.resolve({
                    data: {
                        unified: {
                            contract_version: "2",
                            entity_level: "campaign",
                            rows,
                            totals: null,
                        },
                        salla: { summary: {} },
                    },
                });
            }
            return defaultGet(url, config);
        });
        const bulk = deferred();
        const targeted = deferred();
        getSnapchatEntitySettings.mockImplementation((request) => (
            request.limit === 1 ? targeted.promise : bulk.promise
        ));

        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(container.querySelector(`[data-testid="manage-${targetId}"]`)).not.toBeNull();
        expect(getSnapchatEntitySettings).toHaveBeenCalledWith(expect.objectContaining({
            entityType: "campaign",
            limit: 500,
        }));

        act(() => {
            container.querySelector(`[data-testid="manage-${targetId}"]`).click();
        });
        expect(container.querySelector(
            '[data-testid="snapchat-campaign-management-panel"] > button',
        ).getAttribute("aria-expanded")).toBe("true");
        expect(container.querySelector(
            '[data-testid="snapchat-management-targeted-settings-loading"]',
        ).textContent).toContain("قراءة فقط");
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        )?.disabled ?? true).toBe(true);
        expect(getSnapchatEntitySettings).toHaveBeenCalledWith({
            entityType: "campaign",
            unifiedEntityId: targetId,
            parentUnifiedId: "",
            limit: 1,
        });
        expect(getSnapchatEntitySettings.mock.calls.filter(([request]) => (
            request.entityType === "campaign"
            && request.unifiedEntityId === targetId
            && request.limit === 1
        ))).toHaveLength(1);

        await act(async () => {
            targeted.resolve([completeCampaignSettings(targetId)]);
            await targeted.promise;
            await Promise.resolve();
            await Promise.resolve();
        });
        const currentSettings = container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        );
        expect(currentSettings.textContent).toContain("settings_complete");
        expect(currentSettings.textContent).toContain("50.00 USD");
        expect(currentSettings.textContent).toContain("targeted-settings-complete");

        await act(async () => {
            bulk.resolve([]);
            await bulk.promise;
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        ).textContent).toContain("targeted-settings-complete");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    }, 15_000);

    test("ignores an old targeted response after another campaign is selected", async () => {
        const campaignA = "campaign-target-A";
        const campaignB = "campaign-target-B";
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => {
            if (url.endsWith("/campaigns")) {
                return Promise.resolve({
                    data: {
                        unified: {
                            rows: [campaignRow(campaignA), campaignRow(campaignB)],
                            totals: null,
                        },
                        salla: { summary: {} },
                    },
                });
            }
            return defaultGet(url, config);
        });
        const responseA = deferred();
        const responseB = deferred();
        getSnapchatEntitySettings.mockImplementation((request) => {
            if (request.limit !== 1) return Promise.resolve([]);
            return request.unifiedEntityId === campaignA ? responseA.promise : responseB.promise;
        });

        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        act(() => container.querySelector(`[data-testid="manage-${campaignA}"]`).click());
        act(() => container.querySelector(`[data-testid="manage-${campaignB}"]`).click());

        await act(async () => {
            responseB.resolve([completeCampaignSettings(campaignB, {
                quality: {
                    ...completeCampaignSettings(campaignB).quality,
                    reason: "targeted-B-wins",
                },
            })]);
            await responseB.promise;
            await Promise.resolve();
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        ).textContent).toContain("targeted-B-wins");

        await act(async () => {
            responseA.resolve([completeCampaignSettings(campaignA, {
                quality: {
                    ...completeCampaignSettings(campaignA).quality,
                    reason: "stale-targeted-A",
                },
            })]);
            await responseA.promise;
            await Promise.resolve();
        });
        const currentSettings = container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        );
        expect(currentSettings.textContent).toContain("targeted-B-wins");
        expect(currentSettings.textContent).not.toContain("stale-targeted-A");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("keeps targeted GET transport failure fail-closed and displays its real error", async () => {
        const defaultSettings = getSnapchatEntitySettings.getMockImplementation();
        getSnapchatEntitySettings.mockImplementation((request) => (
            request.limit === 1
                ? Promise.reject(new Error("targeted transport exploded"))
                : defaultSettings(request)
        ));
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="manage-da5049b7-5417-4be9-a596-20a74f9fd54c"]',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const currentSettings = container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        );
        expect(currentSettings.textContent).toContain("settings_sync_failed");
        expect(currentSettings.textContent).toContain("targeted transport exploded");
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("fails closed when targeted identity proof is invalid without trusting financial values", async () => {
        const defaultSettings = getSnapchatEntitySettings.getMockImplementation();
        getSnapchatEntitySettings.mockImplementation((request) => (
            request.limit === 1
                ? Promise.resolve([completeCampaignSettings(
                    "da5049b7-5417-4be9-a596-20a74f9fd54c",
                    {
                        provider_entity_id: "wrong-provider-id",
                        identity_contract: {
                            ...identityContract(),
                            ids_equal: false,
                        },
                    },
                )])
                : defaultSettings(request)
        ));
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="manage-da5049b7-5417-4be9-a596-20a74f9fd54c"]',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const currentSettings = container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        );
        expect(currentSettings.textContent).toContain("settings_sync_failed");
        expect(currentSettings.textContent).toContain("identity_contract غير موثق");
        expect(container.querySelector(
            '[data-testid="snapchat-management-current-daily-budget"]',
        ).textContent).toContain("غير متاح — فشل جلب الإعدادات");
        expect(container.querySelector(
            '[data-testid="snapchat-management-diagnostic-values"]',
        ).textContent).toContain("50.00 USD");
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("preserves the targeted native missing-item status and reason", async () => {
        const targetId = "da5049b7-5417-4be9-a596-20a74f9fd54c";
        const defaultSettings = getSnapchatEntitySettings.getMockImplementation();
        getSnapchatEntitySettings.mockImplementation((request) => (
            request.limit === 1
                ? Promise.resolve([{
                    entity_type: "campaign",
                    unified_entity_id: targetId,
                    provider_entity_id: null,
                    ad_account_id: null,
                    mapping_status: "unverified",
                    mapping_verified: false,
                    identity_contract: {
                        ...identityContract(),
                        ids_equal: null,
                    },
                    quality: {
                        settings_status: "settings_not_loaded",
                        freshness_seconds: null,
                        freshness_threshold_seconds: 1800,
                        reason: "native_entity_row_missing",
                        financial_controls_allowed: false,
                    },
                }])
                : defaultSettings(request)
        ));
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(`[data-testid="manage-${targetId}"]`).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const currentSettings = container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        );
        expect(currentSettings.textContent).toContain("settings_not_loaded");
        expect(currentSettings.textContent).toContain("native_entity_row_missing");
        expect(currentSettings.textContent).toContain("فشل تحقق نطاق القراءة المستهدفة");
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("sanitizes targeted settings returned for another account", async () => {
        const targetId = "da5049b7-5417-4be9-a596-20a74f9fd54c";
        const defaultSettings = getSnapchatEntitySettings.getMockImplementation();
        getSnapchatEntitySettings.mockImplementation((request) => (
            request.limit === 1
                ? Promise.resolve([completeCampaignSettings(targetId, {
                    ad_account_id: "account-other",
                    daily_budget_micro: 50_000_000,
                    daily_budget_usd: 50,
                })])
                : defaultSettings(request)
        ));
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(`[data-testid="manage-${targetId}"]`).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const currentSettings = container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        );
        expect(currentSettings.textContent).toContain("settings_sync_failed");
        expect(currentSettings.textContent).toContain("ad_account_id لا يطابق الحساب المحدد");
        expect(currentSettings.textContent).not.toContain("50.00 USD");
        expect(container.querySelector(
            '[data-testid="snapchat-management-diagnostic-values"]',
        )).toBeNull();
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("uses the selected campaign as the targeted Ad Squad parent", async () => {
        await act(async () => {
            root.render(<SnapchatV2Page />);
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="open-da5049b7-5417-4be9-a596-20a74f9fd54c"]',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="manage-7c0f5bfa-3f59-437b-bb89-1c70b11d0526"]',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(getSnapchatEntitySettings).toHaveBeenCalledWith({
            entityType: "ad_squad",
            unifiedEntityId: "7c0f5bfa-3f59-437b-bb89-1c70b11d0526",
            parentUnifiedId: "da5049b7-5417-4be9-a596-20a74f9fd54c",
            limit: 1,
        });
        expect(getSnapchatEntitySettings.mock.calls.filter(([request]) => (
            request.entityType === "ad_squad"
            && request.unifiedEntityId === "7c0f5bfa-3f59-437b-bb89-1c70b11d0526"
            && request.parentUnifiedId === "da5049b7-5417-4be9-a596-20a74f9fd54c"
            && request.limit === 1
        ))).toHaveLength(1);
        expect(container.querySelector(
            '[data-testid="snapchat-management-current-settings"]',
        ).textContent).toContain("settings_complete");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });
});
