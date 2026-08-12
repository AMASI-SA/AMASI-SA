import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../../services/snapchatCampaignManagement", () => ({
    approveSnapchatManagementProposal: jest.fn(),
    createSnapchatManagementProposal: jest.fn(),
    executeSnapchatManagementProposal: jest.fn(),
    getSnapchatManagementReadiness: jest.fn(),
    listSnapchatManagementProposals: jest.fn(),
    managementError: (error, fallback) => error?.message || fallback,
    microToNativeAmount: (value) => Number(value) / 1_000_000,
    nativeAmountToMicro: (value) => Math.round(Number(value) * 1_000_000),
    pollSnapchatManagementProposal: jest.fn(),
    rollbackSnapchatManagementProposal: jest.fn(),
}));

jest.mock("../../services/mezanProductsV2", () => ({
    listProductsV2: jest.fn(),
}));

import {
    createSnapchatManagementProposal,
    getSnapchatManagementReadiness,
    listSnapchatManagementProposals,
} from "../../services/snapchatCampaignManagement";
import { listProductsV2 } from "../../services/mezanProductsV2";
import SnapchatCampaignManagementPanel from "./SnapchatCampaignManagementPanel";

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

describe("SnapchatCampaignManagementPanel decision context", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        jest.clearAllMocks();
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
        expect(container.querySelector('[data-testid="snapchat-management-product-select"]').value)
            .toBe("710474094");
        expect(previewButton.disabled).toBe(false);

        await act(async () => {
            container.querySelector('[data-testid="snapchat-management-form"]')
                .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(createSnapchatManagementProposal).toHaveBeenCalledWith(expect.objectContaining({
            action: "ad_squad.create",
            account_id: "account-1",
            parent_id: "campaign-1",
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
        }));
    });
});
