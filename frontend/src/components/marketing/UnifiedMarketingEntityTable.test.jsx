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
    test("optional columns align totals and fetch only the visible filtered page", async () => {
        const rows = Array.from({ length: 12 }, (_, i) => row("campaign", `row-${i}`));
        const report = { entity_level: "campaign", rows, totals: rows[0] };
        const onVisibleRowsChange = jest.fn();
        const extraColumns = [{ key: "budget", label: "Daily budget", render: value => `budget-${value.entity.id}` }];
        const render = (value = report) => <UnifiedMarketingEntityTable report={value} pageSize={5} extraColumns={extraColumns} onVisibleRowsChange={onVisibleRowsChange} />;
        await act(async () => root.render(render()));
        expect(onVisibleRowsChange).toHaveBeenCalledTimes(1);
        expect(onVisibleRowsChange.mock.calls[0][0]).toEqual(rows.slice(0, 5));
        expect(container.querySelector("thead tr").children.length).toBe(container.querySelector("tfoot tr").children.length);
        expect(container.querySelector("tbody tr").textContent).toContain("budget-row-0");
        await act(async () => root.render(render()));
        expect(onVisibleRowsChange).toHaveBeenCalledTimes(1);
        await act(async () => [...container.querySelectorAll("footer button")].find(button => button.textContent === "التالي").click());
        expect(onVisibleRowsChange).toHaveBeenLastCalledWith(rows.slice(5, 10));
        const activeButton = [...container.querySelectorAll("button")].find(button => button.textContent === "النشط فقط");
        await act(async () => activeButton.click());
        expect(onVisibleRowsChange).toHaveBeenLastCalledWith(rows.slice(0, 5));
        await act(async () => activeButton.click());
        expect(onVisibleRowsChange).toHaveBeenLastCalledWith(rows.slice(0, 5));
        const next = { ...report, rows: rows.slice(0, 2) };
        await act(async () => root.render(render(next)));
        expect(onVisibleRowsChange).toHaveBeenLastCalledWith(next.rows);
        expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
        expect(onVisibleRowsChange).toHaveBeenCalledTimes(5);
    });

    test("seven-row viewport pins period totals and starts loading when the first batch fits", async () => {
        const rows = Array.from({ length: 21 }, (_, i) => row("campaign", `seven-${i}`));
        const totals = row("campaign", "totals");
        totals.delivery.spend.amount = 777;
        totals.platform_outcomes.conversions = 42;
        const report = { entity_level: "campaign", rows, totals };
        await act(async () => root.render(<UnifiedMarketingEntityTable report={report} pageSize={7} infiniteScroll />));
        const scroll = container.querySelector('[aria-label="جدول الحملات والمجموعات"]');
        const footer = container.querySelector("tfoot");
        expect(container.querySelectorAll("tbody tr")).toHaveLength(7);
        expect(footer.className).toContain("sticky bottom-0");
        expect(footer.textContent).toContain("777.00 USD");
        expect(footer.textContent).toContain("42");
        const initialTotals = footer.textContent;
        const height = scroll.style.height;
        Object.defineProperties(scroll, { scrollHeight: { configurable: true, value: 800 }, clientHeight: { configurable: true, value: 800 } });
        await act(async () => scroll.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: -100 })));
        expect(container.querySelectorAll("tbody tr")).toHaveLength(7);
        await act(async () => scroll.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: 100 })));
        expect(container.querySelectorAll("tbody tr")).toHaveLength(14);
        Object.defineProperty(scroll, "scrollHeight", { configurable: true, value: 1600 });
        scroll.scrollTop = 800;
        await act(async () => scroll.dispatchEvent(new Event("scroll", { bubbles: true })));
        expect(container.querySelectorAll("tbody tr")).toHaveLength(21);
        expect(footer.textContent).toBe(initialTotals);
        expect(scroll.style.height).toBe(height);
    });

    test("Snapchat scroll shows nine then nine, active by default, with global reversible numeric sorting", async () => {
        const rows = Array.from({ length: 23 }, (_, i) => {
            const value = row("campaign", `row-${i}`);
            value.delivery.spend.amount = i;
            value.delivery.impressions = 100 - i;
            return value;
        });
        rows[22].entity.status = "PAUSED"; // contradictory active=true is deliberate
        rows[21].delivery.spend.amount = null;
        const report = { entity_level: "campaign", rows };
        const onVisibleRowsChange = jest.fn();
        await act(async () => root.render(<UnifiedMarketingEntityTable report={report} pageSize={9} infiniteScroll defaultActiveOnly sortable onVisibleRowsChange={onVisibleRowsChange} />));
        const ids = () => [...container.querySelectorAll("tbody tr")].map(tr => tr.querySelector("td div[title]").title);
        expect(ids()).toHaveLength(9);
        expect(ids()[0]).toBe("Entity row-20");
        expect(container.querySelector("footer")).toBeNull();
        const scroll = container.querySelector('[aria-label="جدول الحملات والمجموعات"]');
        Object.defineProperties(scroll, { scrollHeight: { configurable: true, value: 1600 }, clientHeight: { configurable: true, value: 800 } });
        scroll.scrollTop = 800;
        await act(async () => scroll.dispatchEvent(new Event("scroll", { bubbles: true })));
        expect(ids()).toHaveLength(18);
        expect(new Set(ids()).size).toBe(18);
        const fixedHeight = scroll.style.height;
        Object.defineProperty(scroll, "scrollHeight", { configurable: true, value: 2400 });
        scroll.scrollTop = 1600;
        await act(async () => scroll.dispatchEvent(new Event("scroll", { bubbles: true })));
        expect(ids()).toHaveLength(22);
        expect(scroll.style.height).toBe(fixedHeight);
        expect(onVisibleRowsChange.mock.calls.at(-1)[0]).toHaveLength(4);
        await act(async () => container.querySelector('[aria-label="ترتيب حسب الظهور"]').click());
        expect(ids()).toHaveLength(9);
        expect(ids()[0]).toBe("Entity row-0");
        expect(scroll.scrollTop).toBe(0);
        await act(async () => container.querySelector('[aria-label="ترتيب حسب الظهور"]').click());
        expect(ids()[0]).toBe("Entity row-21");
        await act(async () => [...container.querySelectorAll("button")].find(b => b.textContent === "النشط فقط").click());
        expect(ids()[0]).toBe("Entity row-22");
        const paused = container.querySelector("tbody tr td:nth-child(2) span");
        expect(paused.textContent).toBe("PAUSED");
        expect(paused.className).toContain("text-red-700");
        await act(async () => container.querySelector('[aria-label="ترتيب حسب الصرف"]').click());
        expect(ids()[0]).toBe("Entity row-22");
        await act(async () => container.querySelector('[aria-label="ترتيب حسب الصرف"]').click());
        expect(ids()[0]).toBe("Entity row-0");
        expect(ids()).not.toContain("Entity row-21"); // unknown remains last in either direction
    });

    test("settings sort reads all filtered rows before ordering and ignores a late answer after report change", async () => {
        let finish;
        const prepareSort = jest.fn(() => new Promise(resolve => { finish = resolve; }));
        const rows = Array.from({ length: 12 }, (_, i) => row("campaign", `row-${i}`));
        const report = { entity_level: "campaign", rows };
        const extraColumns = [{ key: "budget", label: "Budget", prepareSort, render: () => "—" }];
        const render = report => <UnifiedMarketingEntityTable report={report} extraColumns={extraColumns} pageSize={9} infiniteScroll sortable />;
        await act(async () => root.render(render(report)));
        await act(async () => container.querySelector('[aria-label="ترتيب حسب Budget"]').click());
        expect(prepareSort.mock.calls[0][0]).toEqual(rows);
        await act(async () => finish(Object.fromEntries(rows.map((r, i) => [r.entity.id, i]))));
        expect(container.querySelector("tbody tr").textContent).toContain("Entity row-11");
        await act(async () => container.querySelector('[aria-label="ترتيب حسب Budget"]').click());
        await act(async () => root.render(render({ entity_level: "campaign", rows: [row("campaign", "other-account")] })));
        await act(async () => finish({ "row-11": 999 }));
        expect(container.querySelector("tbody tr").textContent).toContain("other-account");
        expect(container.textContent).not.toContain("جارٍ تجهيز");
    });

});
