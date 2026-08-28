import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../../services/snapchatCampaignManagement", () => ({
    approveSnapchatManagementProposal: jest.fn(),
    clearSnapchatManagementPreviewResume: jest.fn(),
    createSnapchatManagementProposal: jest.fn(),
    diagnoseSnapchatManagementPixels: jest.fn(),
    executeSnapchatManagementProposal: jest.fn(),
    getSnapchatManagementPreviewResume: jest.fn(() => null),
    getSnapchatManagementReadiness: jest.fn(),
    listSnapchatManagementProposals: jest.fn(),
    managementError: (error, fallback) => error?.message || fallback,
    microToNativeAmount: (value) => Number(value) / 1_000_000,
    nativeAmountToMicro: (value) => Math.round(Number(value) * 1_000_000),
    pollSnapchatManagementProposal: jest.fn(),
    reconcileSnapchatManagementProposal: jest.fn(),
    resumeSnapchatManagementProposal: jest.fn(),
    rollbackSnapchatManagementProposal: jest.fn(),
    snapchatBidLabel: (strategy) => strategy === "TARGET_COST" ? "Target Cost" : strategy === "LOWEST_COST_WITH_MAX_BID" ? "Max Bid" : "Bid",
    snapchatFinancialSettingsReady: (settings) => Boolean(
        settings?.mapping_verified
        && ["settings_complete", "complete"].includes(settings?.quality?.settings_status)
        && settings?.quality?.financial_controls_allowed,
    ),
}));

jest.mock("../../context/AuthContext", () => ({
    useOptionalAuth: () => ({ user: { id: "owner-1" } }),
}));

jest.mock("../../services/mezanProductsV2", () => ({
    listProductsV2: jest.fn(),
}));

import {
    approveSnapchatManagementProposal,
    createSnapchatManagementProposal,
    diagnoseSnapchatManagementPixels,
    executeSnapchatManagementProposal,
    getSnapchatManagementPreviewResume,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
    pollSnapchatManagementProposal,
    reconcileSnapchatManagementProposal,
    resumeSnapchatManagementProposal,
} from "../../services/snapchatCampaignManagement";
import { listProductsV2 } from "../../services/mezanProductsV2";
import SnapchatCampaignManagementPanel, { initialForm } from "./SnapchatCampaignManagementPanel";

function change(element, value) {
    const prototype = element instanceof HTMLSelectElement
        ? window.HTMLSelectElement.prototype
        : element instanceof HTMLTextAreaElement
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
}

test("prefills governed update identities selected from the V2 hierarchy", () => {
    expect(initialForm({
        action: "campaign.update",
        selectedCampaign: { campaign_id: "campaign-1", provider_campaign_id: "provider-campaign-1" },
    })).toMatchObject({ targetId: "campaign-1", providerTargetId: "provider-campaign-1" });
    expect(initialForm({
        action: "ad_squad.update",
        selectedCampaign: { campaign_id: "campaign-1", provider_campaign_id: "provider-campaign-1" },
        selectedAdSquad: { ad_squad_id: "squad-1", provider_ad_squad_id: "provider-squad-1" },
    })).toMatchObject({
        targetId: "squad-1",
        providerTargetId: "provider-squad-1",
        parentId: "campaign-1",
        providerParentId: "provider-campaign-1",
    });
    expect(initialForm({
        action: "ad.update",
        selectedAd: { ad_id: "ad-1", ad_squad_id: "squad-1" },
    })).toMatchObject({ targetId: "ad-1", parentId: "squad-1" });
});

describe("SnapchatCampaignManagementPanel decision context", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        jest.clearAllMocks();
        getSnapchatManagementPreviewResume.mockReturnValue(null);
        resumeSnapchatManagementProposal.mockReset();
        getSnapchatManagementReadiness.mockResolvedValue({
            proposal_enabled: true,
            execution_enabled: false,
            activation_enabled: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                currency: "SAR",
                role: "general",
                management_allowed: true,
                creative_allowed: true,
                creative_role: "creative",
                pixels: [{
                    pixel_id: "pixel-1",
                    display_name: "AMASI Pixel",
                    status: "ACTIVE",
                    effective_status: "ACTIVE",
                }],
            }],
        });
        listSnapchatManagementProposals.mockResolvedValue([]);
        listProductsV2.mockResolvedValue({
            items: [{
                mezan_product_id: "mpv2_710474094",
                salla_product_id: "710474094",
                name: "المشط",
                sku: "COMB-1",
                status: "active",
                unlimited_quantity: true,
                variants: [{
                    id: "variant-1",
                    display_name: "أسود",
                    sku: "COMB-BLACK",
                }],
            }],
        });
        createSnapchatManagementProposal.mockResolvedValue({
            proposal_id: "proposal-1",
            action: "ad_squad.create",
            status: "previewed",
            preview: {},
        });
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("requires a catalog product for delivery creation and submits measurable non-authoritative context", async () => {
        await act(async () => {
            root.render(
                <SnapchatCampaignManagementPanel
                    accountId="account-1"
                    entityLevel="campaigns"
                    selectedCampaign={{ campaign_id: "campaign-1" }}
                />,
            );
        });

        await act(async () => {
            container.querySelector('[data-testid="snapchat-campaign-management-panel"] > button').click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(listProductsV2).toHaveBeenCalledWith({
            page: 1,
            perPage: 30,
            query: "",
            status: "active",
        });
        const previewButton = container.querySelector('[data-testid="snapchat-management-create-preview"]');
        expect(previewButton.disabled).toBe(true);

        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-product-select"]'),
                "710474094",
            );
        });
        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-product-variant-select"]'),
                "variant-1",
            );
            change(
                container.querySelector('[data-testid="snapchat-management-sales-direction"]'),
                "stable",
            );
            change(
                container.querySelector('[data-testid="snapchat-management-profit-direction"]'),
                "increase",
            );
            change(
                container.querySelector('[data-testid="snapchat-management-user-context"]'),
                "نزول الرواتب احتمال يحتاج تحقق من النتائج الفعلية",
            );
            change(
                container.querySelector('[data-testid="snapchat-management-trend-override"]'),
                "التحسن الحديث قصير ولم يكتمل إسناد الطلبات",
            );
        });

        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-action-select"]'),
                "ad_squad.create",
            );
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-optimization-goal"]',
        ).value).toBe("PIXEL_PURCHASE");
        expect(container.querySelector(
            '[data-testid="snapchat-management-pixel-select"]',
        ).value).toBe("pixel-1");
        expect(container.querySelector(
            '[data-testid="snapchat-management-conversion-window"]',
        ).value).toBe("SWIPE_28DAY_VIEW_1DAY");
        expect(container.querySelector('[data-testid="snapchat-management-product-select"]').value)
            .toBe("710474094");
        expect(previewButton.disabled).toBe(false);

        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(createSnapchatManagementProposal).toHaveBeenCalledWith(
            expect.objectContaining({
                action: "ad_squad.create",
                account_id: "account-1",
                parent_id: "campaign-1",
                payload: expect.objectContaining({
                    pixel_id: "pixel-1",
                    conversion_window: "SWIPE_28DAY_VIEW_1DAY",
                    optimization_goal: "PIXEL_PURCHASE",
                    delivery_constraint: "DAILY_BUDGET",
                }),
                products: [{
                    product_id: "710474094",
                    product_variant_id: "variant-1",
                    product_name: "المشط",
                }],
                expected_outcome: {
                    primary_goal: "grow_sales_while_protecting_contribution_profit",
                    sales_direction: "stable",
                    contribution_profit_direction: "increase",
                    evaluation_horizons_hours: [24, 72, 168],
                },
                supporting_evidence: [{
                    kind: "user_context",
                    value: "نزول الرواتب احتمال يحتاج تحقق من النتائج الفعلية",
                    source: "snapchat_management_panel:user",
                    verification_status: "user_suggestion",
                    confidence: 0,
                    used_in_decision: false,
                    weight: 0,
                }],
                trend_override_reason: "التحسن الحديث قصير ولم يكتمل إسناد الطلبات",
            }),
            { ownerId: "owner-1" },
        );
    });

    test("discovers a missing Pixel and refreshes the governed readiness list", async () => {
        getSnapchatManagementReadiness
            .mockResolvedValueOnce({
                proposal_enabled: true,
                execution_enabled: true,
                activation_enabled: true,
                accounts: [{
                    account_id: "account-1",
                    display_name: "AMASI",
                    currency: "USD",
                    role: "admin",
                    management_allowed: true,
                    creative_allowed: true,
                    creative_role: "admin",
                    pixels: [],
                }],
            })
            .mockResolvedValueOnce({
                proposal_enabled: true,
                execution_enabled: true,
                activation_enabled: true,
                accounts: [{
                    account_id: "account-1",
                    display_name: "AMASI",
                    currency: "USD",
                    role: "admin",
                    management_allowed: true,
                    creative_allowed: true,
                    creative_role: "admin",
                    pixels: [{
                        pixel_id: "pixel-1",
                        display_name: "AMASI Pixel",
                        status: "ACTIVE",
                        effective_status: "ACTIVE",
                    }],
                }],
            });
        diagnoseSnapchatManagementPixels.mockResolvedValue({
            status: "complete",
            pixels_found: 1,
        });

        await act(async () => {
            root.render(
                <SnapchatCampaignManagementPanel
                    accountId="account-1"
                    entityLevel="ad_squads"
                    selectedCampaign={{ campaign_id: "campaign-1" }}
                />,
            );
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-campaign-management-panel"] > button').click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(container.querySelector('[data-testid="snapchat-management-pixel-select"]').value)
            .toBe("");

        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-discover-pixels"]').click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(diagnoseSnapchatManagementPixels).toHaveBeenCalledWith({ days: 7 });
        expect(getSnapchatManagementReadiness).toHaveBeenCalledTimes(2);
        expect(container.querySelector('[data-testid="snapchat-management-pixel-select"]').value)
            .toBe("pixel-1");
    });

    test("blocks PIXEL optimization without a discovered Pixel but allows a non-Pixel goal", async () => {
        getSnapchatManagementReadiness.mockResolvedValue({
            proposal_enabled: true,
            execution_enabled: false,
            activation_enabled: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                currency: "SAR",
                role: "general",
                management_allowed: true,
                creative_allowed: true,
                creative_role: "creative",
                pixels: [],
            }],
        });
        await act(async () => {
            root.render(
                <SnapchatCampaignManagementPanel
                    accountId="account-1"
                    entityLevel="ad_squads"
                    selectedCampaign={{ campaign_id: "campaign-1" }}
                />,
            );
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-product-select"]'),
                "710474094",
            );
        });

        const previewButton = container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        );
        expect(previewButton.disabled).toBe(true);
        expect(container.querySelector(
            '[data-testid="snapchat-management-pixel-status"]',
        ).textContent).toContain("لا يمكن إنشاء المعاينة");

        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-optimization-goal"]'),
                "SWIPES",
            );
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-pixel-select"]',
        )).toBeNull();
        expect(previewButton.disabled).toBe(false);

        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
            await Promise.resolve();
        });
        const request = createSnapchatManagementProposal.mock.calls[0][0];
        expect(request.payload.optimization_goal).toBe("SWIPES");
        expect(request.payload).not.toHaveProperty("pixel_id");
        expect(request.payload).not.toHaveProperty("conversion_window");
    });

    test("requires explicit selection for an uncertain Pixel then defers to Snapchat eligibility", async () => {
        getSnapchatManagementReadiness.mockResolvedValue({
            proposal_enabled: true,
            execution_enabled: false,
            activation_enabled: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                currency: "USD",
                role: "admin",
                management_allowed: true,
                creative_allowed: true,
                creative_role: "admin",
                pixels: [{
                    pixel_id: "inactive-pixel",
                    display_name: "Inactive Pixel",
                    status: "PAUSED",
                    effective_status: "PAUSED",
                }],
            }],
        });

        await act(async () => {
            root.render(
                <SnapchatCampaignManagementPanel
                    accountId="account-1"
                    entityLevel="ad_squads"
                    selectedCampaign={{ campaign_id: "campaign-1" }}
                />,
            );
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-product-select"]'),
                "710474094",
            );
        });

        const pixelSelect = container.querySelector(
            '[data-testid="snapchat-management-pixel-select"]',
        );
        expect(pixelSelect.value).toBe("");
        expect(pixelSelect.querySelector('option[value="inactive-pixel"]').disabled).toBe(false);
        expect(pixelSelect.querySelector('option[value="inactive-pixel"]').label)
            .toContain("الأهلية تُفحص من Snapchat");
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(true);

        await act(async () => {
            change(pixelSelect, "inactive-pixel");
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(false);
    });

    test("requires an explicit Pixel choice when the account has multiple pixels", async () => {
        getSnapchatManagementReadiness.mockResolvedValue({
            proposal_enabled: true,
            execution_enabled: false,
            activation_enabled: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                currency: "SAR",
                role: "general",
                management_allowed: true,
                creative_allowed: true,
                creative_role: "creative",
                pixels: [
                    {
                        pixel_id: "pixel-1",
                        display_name: "Pixel One",
                        status: "ACTIVE",
                        effective_status: "ACTIVE",
                    },
                    {
                        pixel_id: "pixel-2",
                        display_name: "Pixel Two",
                        status: "ACTIVE",
                        effective_status: "ACTIVE",
                    },
                ],
            }],
        });
        await act(async () => {
            root.render(
                <SnapchatCampaignManagementPanel
                    accountId="account-1"
                    entityLevel="ad_squads"
                    selectedCampaign={{ campaign_id: "campaign-1" }}
                />,
            );
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-product-select"]'),
                "710474094",
            );
        });
        const previewButton = container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        );
        expect(previewButton.disabled).toBe(true);

        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-pixel-select"]'),
                "pixel-2",
            );
            change(
                container.querySelector('[data-testid="snapchat-management-conversion-window"]'),
                "SWIPE_7DAY",
            );
        });
        expect(previewButton.disabled).toBe(false);
        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(createSnapchatManagementProposal).toHaveBeenCalledWith(
            expect.objectContaining({
                payload: expect.objectContaining({
                    pixel_id: "pixel-2",
                    conversion_window: "SWIPE_7DAY",
                }),
            }),
            { ownerId: "owner-1" },
        );
    });

    test("continues from a verified campaign id without consulting the performance report", async () => {
        const campaignId = "b633eafd-f257-44dc-9a3d-26da7ac61fc6";
        listSnapchatManagementProposals.mockResolvedValue([{
            proposal_id: "proposal-campaign-completed",
            action: "campaign.create",
            status: "completed",
            account_id: "account-1",
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
            provider_entity_id: campaignId,
            verified_entity_id: campaignId,
            verification: {
                verified: true,
                entity_id: campaignId,
            },
            products: [{
                product_id: "710474094",
                product_variant_id: "variant-1",
                product_name: "المشط",
            }],
            expected_outcome: {
                sales_direction: "stable",
                contribution_profit_direction: "increase",
            },
            preview: { name: "حملة موثقة" },
        }]);

        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });

        const historyItem = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("proposal"));
        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-user-context"]'),
                "سياق من نموذج آخر يجب ألا ينتقل",
            );
            change(
                container.querySelector('[data-testid="snapchat-management-trend-override"]'),
                "تجاوز من نموذج آخر يجب ألا ينتقل",
            );
        });
        await act(async () => {
            historyItem.click();
        });

        expect(container.querySelector(
            '[data-testid="snapchat-management-verified-entity"]',
        ).textContent).toContain(campaignId);

        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-management-continue-ad-squad"]',
            ).click();
        });

        expect(container.querySelector(
            '[data-testid="snapchat-management-action-select"]',
        ).value).toBe("ad_squad.create");
        expect(container.querySelector(
            '[data-testid="snapchat-management-parent-id"]',
        ).value).toBe(campaignId);
        expect(container.querySelector(
            '[data-testid="snapchat-management-account-select"]',
        ).value).toBe("account-1");
        expect(container.querySelector(
            '[data-testid="snapchat-management-product-select"]',
        ).value).toBe("710474094");
        expect(container.querySelector(
            '[data-testid="snapchat-management-sales-direction"]',
        ).value).toBe("stable");
        expect(container.querySelector(
            '[data-testid="snapchat-management-user-context"]',
        ).value).toBe("");
        expect(container.querySelector(
            '[data-testid="snapchat-management-trend-override"]',
        ).value).toBe("");
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(approveSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("blocks child creation when provider and readback ids are not canonical", async () => {
        listSnapchatManagementProposals.mockResolvedValue([{
            proposal_id: "proposal-campaign-mismatch",
            action: "campaign.create",
            status: "completed",
            account_id: "account-1",
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
            provider_entity_id: "campaign-provider",
            verified_entity_id: null,
            verification: {
                verified: true,
                entity_id: "campaign-readback",
            },
            products: [{ product_id: "710474094", product_name: "المشط" }],
            preview: { name: "حملة غير متطابقة" },
        }]);

        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const historyItem = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("proposal"));
        await act(async () => {
            historyItem.click();
        });

        expect(container.querySelector(
            '[data-testid="snapchat-management-verified-id-blocked"]',
        )).not.toBeNull();
        expect(container.querySelector(
            '[data-testid="snapchat-management-continue-ad-squad"]',
        )).toBeNull();
        expect(container.querySelector(
            '[data-testid="snapchat-management-verification-confirmed"]',
        )).toBeNull();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test.each([
        ["account", { account_id: "" }],
        ["product", { products: [] }],
        ["single product", {
            products: [
                { product_id: "710474094", product_name: "المشط" },
                { product_id: "other-product", product_name: "منتج آخر" },
            ],
        }],
    ])("blocks a historical continuation without one canonical %s", async (_label, change) => {
        const campaignId = "campaign-context-verified";
        listSnapchatManagementProposals.mockResolvedValue([{
            proposal_id: "proposal-context-blocked",
            action: "campaign.create",
            status: "completed",
            account_id: "account-1",
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
            provider_entity_id: campaignId,
            verified_entity_id: campaignId,
            verification: { verified: true, entity_id: campaignId },
            products: [{ product_id: "710474094", product_name: "المشط" }],
            preview: { name: "حملة سياق" },
            ...change,
        }]);

        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const historyItem = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("proposal"));
        await act(async () => {
            historyItem.click();
        });

        expect(container.querySelector(
            '[data-testid="snapchat-management-verified-id-blocked"]',
        )).not.toBeNull();
        expect(container.querySelector(
            '[data-testid="snapchat-management-continue-ad-squad"]',
        )).toBeNull();
    });

    test("continues from a verified ad squad into an ad form without a write", async () => {
        const adSquadId = "ad-squad-verified-1";
        listSnapchatManagementProposals.mockResolvedValue([{
            proposal_id: "proposal-ad-squad-completed",
            action: "ad_squad.create",
            status: "completed",
            account_id: "account-1",
            parent_id: "campaign-verified-1",
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
            provider_entity_id: adSquadId,
            verified_entity_id: adSquadId,
            verification: { verified: true, entity_id: adSquadId },
            products: [{ product_id: "710474094", product_name: "المشط" }],
            preview: { name: "مجموعة موثقة" },
        }]);

        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const historyItem = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("proposal"));
        await act(async () => {
            historyItem.click();
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-management-continue-ad"]',
            ).click();
        });

        expect(container.querySelector(
            '[data-testid="snapchat-management-action-select"]',
        ).value).toBe("ad.create");
        expect(container.querySelector(
            '[data-testid="snapchat-management-parent-id"]',
        ).value).toBe(adSquadId);
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(approveSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("reconciles an uncertain create through the explicit read-only control", async () => {
        const adSquadId = "ad-squad-reconciled-1";
        listSnapchatManagementProposals.mockResolvedValue([{
            proposal_id: "proposal-uncertain-create",
            action: "ad_squad.create",
            status: "failed",
            account_id: "account-1",
            parent_id: "campaign-1",
            provider_write_reached: true,
            provider_write_state: "unknown_needs_reconciliation",
            provider_write_uncertain: true,
            provider_entity_id: null,
            verification: {},
            products: [{ product_id: "710474094", product_name: "المشط" }],
            preview: { name: "مجموعة غير محسومة" },
        }]);
        reconcileSnapchatManagementProposal.mockResolvedValue({
            proposal_id: "proposal-uncertain-create",
            action: "ad_squad.create",
            status: "completed",
            account_id: "account-1",
            parent_id: "campaign-1",
            provider_write_reached: true,
            provider_write_state: "confirmed",
            provider_write_uncertain: false,
            provider_entity_id: adSquadId,
            verified_entity_id: adSquadId,
            verification: { verified: true, entity_id: adSquadId },
            products: [{ product_id: "710474094", product_name: "المشط" }],
            preview: { name: "مجموعة غير محسومة" },
        });

        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const historyItem = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("proposal"));
        await act(async () => {
            historyItem.click();
        });
        const reconcileButton = container.querySelector(
            '[data-testid="snapchat-management-reconcile"]',
        );
        expect(reconcileButton.textContent).toContain("قراءة فقط");
        await act(async () => {
            reconcileButton.click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(reconcileSnapchatManagementProposal)
            .toHaveBeenCalledWith("proposal-uncertain-create");
        expect(container.querySelector(
            '[data-testid="snapchat-management-verified-entity"]',
        ).textContent).toContain(adSquadId);
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(approveSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("opening is GET-only and resumes a saved preview only after an explicit click", async () => {
        getSnapchatManagementPreviewResume.mockReturnValue({
            owner_id: "owner-1",
            idempotency_key: "resume-panel-001",
            preview_job_id: "job-panel-1",
        });
        resumeSnapchatManagementProposal.mockResolvedValue({
            proposal_id: "proposal-panel-1",
            action: "campaign.create",
            status: "previewed",
            confirm_token: "current-token",
            preview: {},
        });
        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        const toggle = container.querySelector(
            '[data-testid="snapchat-campaign-management-panel"] > button',
        );

        await act(async () => {
            toggle.click();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(resumeSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();

        const resumeButton = container.querySelector(
            '[data-testid="snapchat-management-resume-preview"]',
        );
        expect(resumeButton).not.toBeNull();
        await act(async () => {
            resumeButton.click();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(resumeSnapchatManagementProposal).toHaveBeenCalledTimes(1);
        expect(container.querySelector(
            '[data-testid="snapchat-management-approve"]',
        )).not.toBeNull();
        expect(approveSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("reopening during create follows the same pending preview without auto action", async () => {
        let finishPreview;
        const sharedPreview = new Promise((resolve) => {
            finishPreview = resolve;
        });
        createSnapchatManagementProposal.mockReturnValue(sharedPreview);
        resumeSnapchatManagementProposal.mockReturnValue(sharedPreview);
        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        const toggle = container.querySelector(
            '[data-testid="snapchat-campaign-management-panel"] > button',
        );
        await act(async () => {
            toggle.click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-product-select"]'),
                "710474094",
            );
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
        });
        expect(createSnapchatManagementProposal).toHaveBeenCalledTimes(1);

        getSnapchatManagementPreviewResume.mockReturnValue({
            owner_id: "owner-1",
            idempotency_key: "created-preview-key",
            preview_job_id: "created-preview-job",
        });
        await act(async () => {
            toggle.click();
            await Promise.resolve();
        });
        await act(async () => {
            toggle.click();
            await Promise.resolve();
        });
        expect(resumeSnapchatManagementProposal).not.toHaveBeenCalled();

        await act(async () => {
            finishPreview({
                proposal_id: "proposal-create-reopen",
                action: "campaign.create",
                status: "previewed",
                confirm_token: "single-token",
                preview: {},
            });
            await sharedPreview;
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-approve"]',
        )).not.toBeNull();
        expect(approveSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("collapse and reopen keeps an in-flight approval locked", async () => {
        getSnapchatManagementPreviewResume.mockReturnValue({
            owner_id: "owner-1",
            idempotency_key: "approval-lock-001",
            preview_job_id: "job-approval-lock",
        });
        resumeSnapchatManagementProposal.mockResolvedValue({
            proposal_id: "proposal-approval-lock",
            action: "campaign.create",
            status: "previewed",
            confirm_token: "approval-token",
            preview: {},
        });
        let finishApproval;
        approveSnapchatManagementProposal.mockReturnValue(new Promise((resolve) => {
            finishApproval = resolve;
        }));
        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        const toggle = container.querySelector(
            '[data-testid="snapchat-campaign-management-panel"] > button',
        );
        await act(async () => {
            toggle.click();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(resumeSnapchatManagementProposal).not.toHaveBeenCalled();
        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-resume-preview"]').click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-approve"]').click();
            await Promise.resolve();
        });
        expect(approveSnapchatManagementProposal).toHaveBeenCalledTimes(1);

        await act(async () => {
            toggle.click();
            await Promise.resolve();
        });
        await act(async () => {
            toggle.click();
            await Promise.resolve();
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-approve"]',
        ).disabled).toBe(true);
        expect(resumeSnapchatManagementProposal).toHaveBeenCalledTimes(1);
        expect(approveSnapchatManagementProposal).toHaveBeenCalledTimes(1);

        await act(async () => {
            finishApproval({
                proposal_id: "proposal-approval-lock",
                action: "campaign.create",
                status: "approved",
                preview: {},
            });
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(approveSnapchatManagementProposal).toHaveBeenCalledTimes(1);
    });

    test("collapse and reopen keeps an in-flight execution locked", async () => {
        getSnapchatManagementReadiness.mockResolvedValue({
            proposal_enabled: true,
            execution_enabled: true,
            activation_enabled: false,
            accounts: [{
                account_id: "account-1",
                display_name: "AMASI",
                currency: "SAR",
                role: "general",
                management_allowed: true,
                creative_allowed: true,
                creative_role: "creative",
                pixels: [{ pixel_id: "pixel-1", display_name: "AMASI Pixel" }],
            }],
        });
        createSnapchatManagementProposal.mockResolvedValue({
            proposal_id: "proposal-execution-lock",
            action: "campaign.create",
            status: "previewed",
            confirm_token: "execution-approval-token",
            preview: {},
        });
        approveSnapchatManagementProposal.mockResolvedValue({
            proposal_id: "proposal-execution-lock",
            action: "campaign.create",
            status: "approved",
            preview: {},
        });
        let finishExecution;
        executeSnapchatManagementProposal.mockReturnValue(new Promise((resolve) => {
            finishExecution = resolve;
        }));
        pollSnapchatManagementProposal.mockResolvedValue({
            proposal: {
                proposal_id: "proposal-execution-lock",
                action: "campaign.create",
                status: "completed",
            },
            proposals: [],
        });
        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        const toggle = container.querySelector(
            '[data-testid="snapchat-campaign-management-panel"] > button',
        );
        await act(async () => {
            toggle.click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            change(
                container.querySelector('[data-testid="snapchat-management-product-select"]'),
                "710474094",
            );
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-approve"]').click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-execute"]').click();
            await Promise.resolve();
        });
        expect(executeSnapchatManagementProposal).toHaveBeenCalledTimes(1);

        await act(async () => {
            toggle.click();
            await Promise.resolve();
        });
        await act(async () => {
            toggle.click();
            await Promise.resolve();
        });
        expect(container.querySelector(
            '[data-testid="snapchat-management-execute"]',
        ).disabled).toBe(true);
        expect(executeSnapchatManagementProposal).toHaveBeenCalledTimes(1);
        expect(resumeSnapchatManagementProposal).not.toHaveBeenCalled();

        await act(async () => {
            finishExecution({
                proposal_id: "proposal-execution-lock",
                action: "campaign.create",
                status: "executing",
            });
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(executeSnapchatManagementProposal).toHaveBeenCalledTimes(1);
    });

    test("shows current provider settings separately and submits only optional new financial values with provider IDs", async () => {
        const currentSettings = {
            unified_entity_id: "unified-squad-1",
            provider_entity_id: "provider-squad-9",
            provider_parent_id: "provider-campaign-8",
            mapping_verified: true,
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
            settings_synced_at: "2026-08-28T10:00:00Z",
            provider_updated_at: "2026-08-28T09:55:00Z",
            quality: {
                settings_status: "settings_complete",
                freshness_seconds: 120,
                reason: "provider_snapshot_complete",
                financial_controls_allowed: true,
            },
        };
        await act(async () => {
            root.render(
                <SnapchatCampaignManagementPanel
                    accountId="account-1"
                    entityLevel="ad_squads"
                    initialAction="ad_squad.update"
                    selectedCampaign={{
                        campaign_id: "unified-campaign-1",
                        provider_campaign_id: "provider-campaign-8",
                    }}
                    selectedAdSquad={{
                        ad_squad_id: "unified-squad-1",
                        provider_ad_squad_id: "provider-squad-9",
                    }}
                    currentSettings={currentSettings}
                />,
            );
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-campaign-management-panel"] > button').click();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(container.querySelector('[data-testid="snapchat-management-current-settings"]').textContent)
            .toContain("50.00 USD");
        expect(container.querySelector('[data-testid="snapchat-management-current-bid-label"]').textContent)
            .toContain("Max Bid");
        expect(container.querySelector('[data-testid="snapchat-management-new-daily-budget"]').value).toBe("");
        expect(container.querySelector('[data-testid="snapchat-management-new-bid"]').value).toBe("");
        expect(container.querySelector('[data-testid="snapchat-management-provider-target-id"]').value)
            .toBe("provider-squad-9");

        await act(async () => {
            change(container.querySelector('[data-testid="snapchat-management-new-daily-budget"]'), "60");
            change(container.querySelector('[data-testid="snapchat-management-new-bid"]'), "8");
            change(container.querySelector('[data-testid="snapchat-management-new-bid-strategy"]'), "LOWEST_COST_WITH_MAX_BID");
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(createSnapchatManagementProposal).toHaveBeenCalledWith(
            expect.objectContaining({
                target_id: "unified-squad-1",
                provider_target_id: "provider-squad-9",
                parent_id: "unified-campaign-1",
                provider_parent_id: "provider-campaign-8",
                settings_proof: expect.objectContaining({
                    unified_entity_id: "unified-squad-1",
                    provider_entity_id: "provider-squad-9",
                    settings_status: "settings_complete",
                }),
                payload: expect.objectContaining({
                    daily_budget_micro: 60_000_000,
                    bid_micro: 8_000_000,
                    bid_strategy: "LOWEST_COST_WITH_MAX_BID",
                }),
            }),
            { ownerId: "owner-1" },
        );
    });

    test("blocks a financial preview when provider settings are stale", async () => {
        const staleSettings = {
            unified_entity_id: "unified-campaign-1",
            provider_entity_id: "provider-campaign-1",
            mapping_verified: true,
            account_currency: "USD",
            daily_budget_micro: 40_000_000,
            daily_budget_usd: 40,
            quality: {
                settings_status: "settings_stale",
                freshness_seconds: 7200,
                reason: "older_than_freshness_limit",
                financial_controls_allowed: false,
            },
        };
        await act(async () => {
            root.render(
                <SnapchatCampaignManagementPanel
                    accountId="account-1"
                    entityLevel="campaigns"
                    initialAction="campaign.update"
                    selectedCampaign={{
                        campaign_id: "unified-campaign-1",
                        provider_campaign_id: "provider-campaign-1",
                    }}
                    currentSettings={staleSettings}
                />,
            );
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-campaign-management-panel"] > button').click();
            await Promise.resolve();
            await Promise.resolve();
        });
        await act(async () => {
            change(container.querySelector('[data-testid="snapchat-management-new-daily-budget"]'), "45");
        });
        expect(container.querySelector('[data-testid="snapchat-management-financial-settings-blocked"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="snapchat-management-create-preview"]').disabled).toBe(true);
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
        expect(executeSnapchatManagementProposal).not.toHaveBeenCalled();
    });

    test("renders structured before/after audit values and verified provider readback", async () => {
        listSnapchatManagementProposals.mockResolvedValue([{
            proposal_id: "proposal-audit-1",
            action: "ad_squad.update",
            status: "completed",
            created_at: "2026-08-28T10:00:00Z",
            executed_at: "2026-08-28T10:01:00Z",
            actor_id: "owner-1",
            provider_entity_id: "provider-squad-9",
            verification: { verified: true, entity_id: "provider-squad-9" },
            field_changes: [{
                field: "daily_budget_micro",
                before: 50_000_000,
                after: 60_000_000,
                before_usd: 50,
                after_usd: 60,
            }, {
                field: "bid_strategy",
                before: "TARGET_COST",
                after: "LOWEST_COST_WITH_MAX_BID",
            }],
            preview: { changed_fields: ["daily_budget_micro", "bid_strategy"] },
        }]);
        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        await act(async () => {
            container.querySelector('[data-testid="snapchat-campaign-management-panel"] > button').click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const logButton = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("proposal-audit-1".slice(0, 8)));
        await act(async () => {
            logButton.click();
            await Promise.resolve();
        });
        const changes = container.querySelector('[data-testid="snapchat-management-field-changes"]');
        expect(changes.textContent).toContain("50,000,000 micro");
        expect(changes.textContent).toContain("60 USD");
        expect(changes.textContent).toContain("TARGET_COST");
        expect(changes.textContent).toContain("LOWEST_COST_WITH_MAX_BID");
        expect(container.querySelector('[data-testid="snapchat-management-audit-metadata"]').textContent)
            .toContain("provider-squad-9");
        expect(container.querySelector('[data-testid="snapchat-management-audit-metadata"]').textContent)
            .toContain("مطابقة مؤكدة");
    });

    test("keeps a GET-only resume control after the preview polling timeout", async () => {
        getSnapchatManagementPreviewResume.mockReturnValue({
            owner_id: "owner-1",
            idempotency_key: "resume-timeout-001",
            preview_job_id: "job-timeout-1",
        });
        const timeout = Object.assign(new Error("ما زال تجهيز المعاينة مستمرًا"), {
            code: "snapchat_management_preview_poll_timeout",
        });
        resumeSnapchatManagementProposal.mockRejectedValue(timeout);
        await act(async () => {
            root.render(<SnapchatCampaignManagementPanel accountId="account-1" />);
        });
        await act(async () => {
            container.querySelector(
                '[data-testid="snapchat-campaign-management-panel"] > button',
            ).click();
            await Promise.resolve();
            await Promise.resolve();
        });
        const resumeButton = container.querySelector(
            '[data-testid="snapchat-management-resume-preview"]',
        );
        expect(resumeButton).not.toBeNull();
        expect(container.querySelector(
            '[data-testid="snapchat-management-create-preview"]',
        ).disabled).toBe(true);

        await act(async () => {
            resumeButton.click();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(resumeSnapchatManagementProposal).toHaveBeenCalledTimes(1);
        expect(createSnapchatManagementProposal).not.toHaveBeenCalled();
    });
});
