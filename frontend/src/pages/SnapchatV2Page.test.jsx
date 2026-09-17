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
    function MockUnifiedMarketingEntityTable(props) {
        const { report, onOpenChildren, onManageEntity } = props;
        const RealTable = jest.requireActual("../components/marketing/UnifiedMarketingEntityTable").default;
        return (
            <div data-testid="mock-unified-table">
                <RealTable {...props} />
                {(report?.rows || []).map((row) => (
                    <div key={row.entity.id}>
                    <button
                        key={row.entity.id}
                        type="button"
                        data-testid={`open-${row.entity.id}`}
                        onClick={() => onOpenChildren?.(row)}
                    >
                        {row.entity.name}
                    </button>
                    <button data-testid={`manage-${row.entity.id}`} onClick={() => onManageEntity?.(row)}>Manage</button>
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

describe("SnapchatV2Page read-only load", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        Element.prototype.scrollIntoView = jest.fn();
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
                                delivery: {},
                                platform_outcomes: {},
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
                                delivery: {},
                                platform_outcomes: {},
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

    test("last 30 days preset submits the exact rolling sync range", async () => {
        const clock = jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-09-17T12:00:00Z"));
        api.post.mockResolvedValue({ data: { status: "complete" } });
        try {
            await act(async () => root.render(<SnapchatV2Page />));
            const button = (text) => Array.from(container.querySelectorAll("button")).find((item) => item.textContent.trim() === text);
            await act(async () => button("آخر 30 يومًا").click());
            expect(Array.from(container.querySelectorAll('input[type="date"]')).map((item) => item.value)).toEqual(["2026-08-19", "2026-09-17"]);
            expect(button("مزامنة V2").disabled).toBe(false);
            await act(async () => button("مزامنة V2").click());
            expect(api.post).toHaveBeenCalledTimes(1);
            expect(api.post).toHaveBeenCalledWith("/integrations-v2/snapchat-v2/sync", expect.objectContaining({
                date_from: "2026-08-19", date_to: "2026-09-17", run_type: "manual",
            }));
        } finally {
            clock.mockRestore();
        }
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
        expect(container.querySelector('[data-column="daily-budget"]').textContent).toBe("غير متاح على مستوى الحملة");
        expect(container.querySelector('[data-testid="snapchat-entity-settings-table"]')).toBeNull();
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
        expect(container.querySelector('[data-column="daily-budget"]').textContent).toContain("125.00 USD");
        expect(container.querySelector('[data-column="bid-target-cost"]').textContent).toContain("Target Cost15.00 USD");

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
        expect(container.textContent).toContain("تعذّر جلب الإعدادات");
        expect(container.querySelector('[data-column="daily-budget"] span').title).toContain("فشل إثبات ارتباط إعدادات الكيان بالحساب الإعلاني المحدد");
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
                daily_budget_micro: 72_000_000,
                daily_budget_availability: "available",
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
        expect(container.querySelector('[data-column="daily-budget"]').textContent).toContain("72.00 USD");

        await act(async () => {
            accountASettings.resolve([{
                entity_type: "campaign",
                unified_entity_id: "da5049b7-5417-4be9-a596-20a74f9fd54c",
                provider_entity_id: "provider-campaign-A",
                mapping_status: "verified",
                mapping_verified: true,
                ad_account_id: "account-A",
                account_currency: "USD",
                daily_budget_micro: 91_000_000,
                daily_budget_availability: "available",
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

        expect(container.querySelector('[data-column="daily-budget"]').textContent).toContain("72.00 USD");
        expect(container.querySelector('[data-column="daily-budget"]').textContent).not.toContain("91.00 USD");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });
    it("sorts budgets across all filtered campaigns and reuses previously loaded settings", async () => {
        const rows = Array.from({ length: 20 }, (_, i) => ({
            delivery: {}, platform_outcomes: {},
            entity: { id: `campaign-${i}`, level: "campaign", name: `Campaign ${i}`, status: "ACTIVE" },
            quality: { sync_status: "complete", coverage_status: "complete" },
        }));
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => url.endsWith("/campaigns")
            ? Promise.resolve({ data: { unified: { entity_level: "campaign", rows }, salla: { summary: {} } } })
            : defaultGet(url, config));
        getSnapchatEntitySettings.mockImplementation(({ unifiedEntityId }) => Promise.resolve([{
            unified_entity_id: unifiedEntityId, entity_type: "campaign", ad_account_id: "account-1",
            daily_budget_micro: Number(unifiedEntityId.split("-")[1]) * 1_000_000,
            account_currency: "USD", quality: { settings_status: "settings_complete" },
        }]));
        await act(async () => root.render(<SnapchatV2Page />));
        expect(getSnapchatEntitySettings).toHaveBeenCalledTimes(7);
        await act(async () => container.querySelector('[aria-label="ترتيب حسب ميزانية الحملة اليومية"]').click());
        const table = container.querySelector('[data-testid="unified-marketing-entity-table"]');
        expect(table.querySelector('tbody tr').textContent).toContain("Campaign 19");
        expect(table.querySelector('[data-column="daily-budget"]').textContent).toContain("19.00 USD");
        expect(getSnapchatEntitySettings).toHaveBeenCalledTimes(20);
        await act(async () => container.querySelector('[aria-label="ترتيب حسب ميزانية الحملة اليومية"]').click());
        expect(table.querySelector('tbody tr').textContent).toContain("Campaign 0");
        expect(getSnapchatEntitySettings).toHaveBeenCalledTimes(20);
        expect(api.post).not.toHaveBeenCalled();
    });

    it("reads only seven visible exact IDs and fetches an off-page management selection precisely", async () => {
        const rows = Array.from({ length: 12 }, (_, i) => ({
            delivery: {}, platform_outcomes: {},
            entity: { id: `campaign-${i}`, level: "campaign", provider_level: "campaign", name: `Campaign ${i}`, status: "ACTIVE" },
            quality: { sync_status: "complete", coverage_status: "complete" },
        }));
        const defaultGet = api.get.getMockImplementation();
        api.get.mockImplementation((url, config) => url.endsWith("/campaigns")
            ? Promise.resolve({ data: { unified: { entity_level: "campaign", rows }, salla: { summary: {} } } })
            : defaultGet(url, config));
        getSnapchatEntitySettings.mockImplementation(({ unifiedEntityId }) => Promise.resolve([{
            unified_entity_id: unifiedEntityId,
            entity_type: "campaign",
            provider_entity_id: `provider-${unifiedEntityId}`,
            ad_account_id: "account-1",
            quality: { settings_status: "settings_complete" },
        }]));
        await act(async () => { root.render(<SnapchatV2Page />); });
        expect(getSnapchatEntitySettings.mock.calls.map(([params]) => params.unifiedEntityId))
            .toEqual(rows.slice(0, 7).map(row => row.entity.id));
        expect(getSnapchatEntitySettings.mock.calls.every(([params]) => params.limit === 1)).toBe(true);
        const table = container.querySelector('[data-testid="unified-marketing-entity-table"]');
        expect(table.querySelectorAll('tbody tr')).toHaveLength(7);
        const scroll = table.querySelector('[aria-label="جدول الحملات والمجموعات"]');
        Object.defineProperties(scroll, { scrollHeight: { value: 1600 }, clientHeight: { value: 800 } });
        scroll.scrollTop = 800;
        await act(async () => { scroll.dispatchEvent(new Event("scroll", { bubbles: true })); });
        expect(table.querySelectorAll('tbody tr')).toHaveLength(12);
        expect(table.querySelector('[data-column="daily-budget"]')).not.toBeNull();
        expect(getSnapchatEntitySettings.mock.calls.slice(7).map(([params]) => params.unifiedEntityId))
            .toEqual(rows.slice(7, 12).map(row => row.entity.id));
        await act(async () => { container.querySelector('[data-testid="manage-campaign-11"]').click(); });
        expect(getSnapchatEntitySettings).toHaveBeenLastCalledWith(expect.objectContaining({
            entityType: "campaign", unifiedEntityId: "campaign-11", limit: 1,
        }));
        const older = deferred();
        const newer = deferred();
        getSnapchatEntitySettings.mockImplementationOnce(() => older.promise).mockImplementationOnce(() => newer.promise);
        await act(async () => { container.querySelector('[data-testid="manage-campaign-10"]').click(); });
        await act(async () => { container.querySelector('[data-testid="manage-campaign-11"]').click(); });
        const selection = (id) => [{ unified_entity_id: id, provider_entity_id: `selected-${id}`,
            ad_account_id: "account-1", quality: { settings_status: "settings_complete" } }];
        await act(async () => { newer.resolve(selection("campaign-11")); });
        await act(async () => { older.resolve(selection("campaign-10")); });
        await act(async () => { container.querySelector('[data-testid="snapchat-campaign-management-panel"] > button').click(); });
        const card = container.querySelector('[data-testid="snapchat-management-current-settings"]');
        expect(card.textContent).toContain("selected-campaign-11");
        expect(card.textContent).not.toContain("selected-campaign-10");
        expect(api.post).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

});
