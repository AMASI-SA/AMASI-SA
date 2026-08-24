import React, { act } from "react";
import { createRoot } from "react-dom/client";

import UnifiedMarketingEntityTable from "./UnifiedMarketingEntityTable";

function money(amount, currency) {
    return { amount, currency };
}

function row(level, id, commerceStatus = "complete") {
    return {
        provider: "snapchat_ads",
        entity: {
            level,
            provider_level: level === "ad_group" ? "ad_squad" : level,
            id,
            name: `Entity ${id}`,
            status: "ACTIVE",
            active: true,
            campaign_id: "campaign-1",
            ad_group_id: level === "ad_group" ? id : "squad-1",
        },
        delivery: {
            spend: money(10, "USD"),
            spend_sar: money(37.5, "SAR"),
            impressions: 100,
            clicks: 5,
            views: 25,
        },
        platform_outcomes: {
            conversions: 2,
            revenue: money(40, "USD"),
            roas: 4,
        },
        commerce_outcomes: {
            status: commerceStatus,
            orders: commerceStatus === "complete" ? 3 : null,
            revenue: money(commerceStatus === "complete" ? 150 : null, "SAR"),
            roas: commerceStatus === "complete" ? 4 : null,
        },
        commerce_profitability: {
            status: "partial",
            orders: 3,
            sales: money(150, "SAR"),
            product_cost: money(null, "SAR"),
            known_product_cost: money(40, "SAR"),
            ad_spend: money(37.5, "SAR"),
            contribution_profit: money(null, "SAR"),
            profit_margin_pct: null,
            cost_status: "missing",
            missing_cost_orders: 1,
            product_count: 1,
            products: [{
                identity: "product-1",
                salla_product_id: "product-1",
                mezan_product_id: "mezan-product-1",
                name: "منتج حملة سناب",
                sku: "SKU-1",
                image_url: null,
                units: 2,
                orders: 1,
                sales: money(150, "SAR"),
                product_cost: money(null, "SAR"),
                allocated_ad_spend: money(37.5, "SAR"),
                contribution_profit: money(null, "SAR"),
                profit_margin_pct: null,
                cost_status: "missing",
            }],
        },
        abandoned_cart_outcomes: {
            status: level === "campaign" ? "complete" : "unavailable",
            scope: "exact_cart_campaign_id_match",
            cart_snapshots: 5,
            abandoned_carts: 4,
            recovered_carts: 1,
            abandoned_value: money(420, "SAR"),
            is_campaign_attributed: level === "campaign",
            top_products: [{
                product_id: "cart-product-1",
                name: "منتج سلة متروكة",
                abandoned_carts: 3,
                units: 4,
                value: money(300, "SAR"),
            }],
        },
        quality: {
            sync_status: "complete",
            coverage_status: "complete",
        },
    };
}

describe("UnifiedMarketingEntityTable", () => {
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

    test("renders the provider-neutral metrics and opens the next hierarchy level", async () => {
        const onOpenChildren = jest.fn();
        const onManageEntity = jest.fn();
        const campaign = row("campaign", "campaign-1");
        await act(async () => {
            root.render(
                <UnifiedMarketingEntityTable
                    report={{
                        contract_version: "unified-marketing-data-v1",
                        entity_level: "campaign",
                        rows: [campaign],
                        totals: campaign,
                    }}
                    onOpenChildren={onOpenChildren}
                    onManageEntity={onManageEntity}
                />,
            );
        });

        expect(container.textContent).toContain("10.00 USD");
        expect(container.textContent).toContain("150.00 SAR");
        expect(container.textContent).toContain("تكلفة الطلب حسب Snapchat");
        expect(container.textContent).toContain("5.00 USD");
        expect(container.textContent).toContain("تكلفة الطلب حسب سلة");
        expect(container.textContent).toContain("12.50 SAR");
        const button = Array.from(container.querySelectorAll("button"))
            .find((item) => item.textContent.includes("Ad Squads"));
        await act(async () => button.click());
        expect(onOpenChildren).toHaveBeenCalledWith(campaign);
        const manageButton = Array.from(container.querySelectorAll("button"))
            .find((item) => item.textContent.includes("تعديل / حالة"));
        await act(async () => manageButton.click());
        expect(onManageEntity).toHaveBeenCalledWith(campaign);
    });

    test("renders unavailable ad-level Salla attribution as unknown, not zero", async () => {
        const ad = row("ad", "ad-1", "unavailable");
        ad.platform_outcomes.conversions = 0;
        await act(async () => {
            root.render(
                <UnifiedMarketingEntityTable
                    report={{
                        contract_version: "unified-marketing-data-v1",
                        entity_level: "ad",
                        rows: [ad],
                        totals: ad,
                    }}
                />,
            );
        });

        const headers = Array.from(container.querySelectorAll("thead th"));
        const snapchatCostIndex = headers.findIndex((item) => item.textContent === "تكلفة الطلب حسب Snapchat");
        const sallaCostIndex = headers.findIndex((item) => item.textContent === "تكلفة الطلب حسب سلة");
        const cells = container.querySelectorAll("tbody tr:first-child td");
        expect(cells[snapchatCostIndex].textContent).toBe("—");
        expect(cells[sallaCostIndex].textContent).toBe("—");
        expect(container.textContent).toContain("—");
    });

    test("opens campaign products and links missing cost to the product workspace", async () => {
        const campaign = row("campaign", "campaign-1");
        await act(async () => {
            root.render(<UnifiedMarketingEntityTable report={{ entity_level: "campaign", rows: [campaign], totals: campaign }} />);
        });
        const button = Array.from(container.querySelectorAll("button"))
            .find((item) => item.textContent.includes("تكلفة ناقصة"));
        await act(async () => button.click());
        expect(container.textContent).toContain("منتج حملة سناب");
        expect(container.textContent).toContain("فتح المنتج وإضافة التكلفة");
        const link = container.querySelector('a[href*="/products-v2"]');
        expect(link.getAttribute("href")).toContain("product=mezan-product-1");
        expect(container.textContent).toContain("تكلفة المنتجات—");
    });

    test("shows only campaign-attributed abandoned carts as intent evidence", async () => {
        const campaign = row("campaign", "campaign-1");
        await act(async () => root.render(<UnifiedMarketingEntityTable report={{ entity_level: "campaign", rows: [campaign], totals: campaign }} />));
        const button = Array.from(container.querySelectorAll("button"))
            .find((item) => item.textContent.includes("4 متروكة"));
        await act(async () => button.click());
        expect(container.textContent).toContain("منتج سلة متروكة");
        expect(container.textContent).toContain("ليست مبيعات أو ربحًا للحملة");
        expect(container.textContent).toContain("420.00 SAR");
    });
});
