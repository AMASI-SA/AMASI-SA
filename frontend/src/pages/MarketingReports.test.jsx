import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import MarketingReports, { ProductDialog } from "./MarketingReports";
import api from "../lib/api";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router/dist/development/index.js"); });

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock("./SnapchatV2Page", () => ({ snapchatSyncRangeAllowed: () => true }));
jest.mock("../campaignProfitabilityProductNavigation", () => ({ buildProfitabilityProductCostHref: ({ productId }) => `/products-v2/${productId}` }));

let host, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); jest.clearAllMocks(); });
afterEach(() => { act(() => root.unmount()); host.remove(); });
const metrics = { orders: 49, spend_sar: 1670.06, sales_sar: 1835.67, product_cost_sar: 608.4, shipping_cost_sar: 172.5, payment_fees_sar: 55.07, net_profit_sar: -670.36, net_margin_pct: -36.52 };
const row = { id: "campaign", name: "Copy of Sales", active: true, campaign_id: "campaign", metrics,
    profitability: { products: [{ identity: "p", mezan_product_id: "p", name: "منتج اختباري", sku: "SKU-1", units: 1, sales_sar: 1835.67, product_cost_sar: 608.4, allocated_ad_spend_sar: 1670.06, shipping_cost_sar: 172.5, payment_fees_sar: 55.07, net_profit_sar: -670.36, net_margin_pct: -36.52 }] } };
const payload = source => ({ source, provider: "snapchat", range: { date_from: "2026-10-01", date_to: "2026-10-01", timezone: "America/Los_Angeles" }, generated_at: "2026-10-01T12:00:00Z",
    reports: [{ provider: "snapchat", status: "available", selected_account_id: "a", accounts: [{ id: "a", name: "حساب", currency: "USD", timezone: "America/Los_Angeles" }], rows: [{ ...row, metrics: { ...metrics, orders: source === "platform" ? 68 : 49 } }], totals: metrics }], totals: { ...metrics, orders: source === "platform" ? 68 : 49 } });
async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }

test("product opens a new tab and modal has complete direct-cost columns and Escape close", () => {
    const close = jest.fn();
    act(() => root.render(<ProductDialog row={row} source="salla" provider="snapchat" onSource={() => {}} onClose={close} />));
    const link = host.querySelector('a[href="/products-v2/p"]');
    expect(link.target).toBe("_blank"); expect(link.rel).toBe("noopener noreferrer");
    expect(host.textContent).toContain("تكلفة الشحن"); expect(host.textContent).toContain("رسوم الدفع"); expect(host.textContent).toContain("-670.36");
    act(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(close).toHaveBeenCalledTimes(1);
});

test("provider source defaults to 68 and switches to actual Salla 49", async () => {
    api.get.mockImplementation((url, opts) => Promise.resolve({ data: payload(opts.params.source) }));
    await act(async () => root.render(<MemoryRouter initialEntries={["/ads-manager/reports?provider=snapchat"]}><MarketingReports /></MemoryRouter>));
    await flush();
    expect(api.get.mock.calls[0][0]).toBe("/marketing-reports");
    expect(host.querySelector("tfoot").textContent).toContain("68");
    const button = [...host.querySelectorAll("button")].find(x => x.textContent === "عرض نتائج سلة");
    await act(async () => button.click()); await flush();
    expect(api.get.mock.calls.at(-1)[1].params.source).toBe("salla");
    expect(host.querySelector("tfoot").textContent).toContain("49");
    expect(host.textContent).toContain("التقارير");
});

test("all platforms display one row per platform and drill into campaigns", async () => {
    api.get.mockResolvedValue({ data: { ...payload("platform"), provider: "all", reports: ["snapchat", "tiktok"].map(provider => ({ provider, label: provider, status: "available", totals: metrics })) } });
    await act(async () => root.render(<MemoryRouter initialEntries={["/ads-manager/reports?provider=all"]}><MarketingReports /></MemoryRouter>)); await flush();
    expect(host.querySelectorAll("tbody tr")).toHaveLength(2);
    const snap = [...host.querySelectorAll("tbody button")].find(x => x.textContent === "snapchat");
    api.get.mockResolvedValue({ data: payload("platform") });
    await act(async () => snap.click()); await flush();
    expect(api.get.mock.calls.at(-1)[1].params.provider).toBe("snapchat");
    expect(host.textContent).toContain("Copy of Sales");
});

test("a slower old request cannot overwrite a newly selected source", async () => {
    let resolveOld;
    api.get.mockImplementation((url, opts) => opts.params.source === "platform" ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve({ data: payload("salla") }));
    await act(async () => root.render(<MemoryRouter><MarketingReports /></MemoryRouter>));
    const button = [...host.querySelectorAll("button")].find(x => x.textContent === "عرض نتائج سلة");
    await act(async () => button.click()); await flush();
    await act(async () => resolveOld({ data: payload("platform") }));
    expect(host.querySelector("tfoot").textContent).toContain("49");
    expect(host.querySelector("tfoot").textContent).not.toContain("68");
});

test("direct and unclassified are standalone Salla sources without ad source toggle", async () => {
    api.get.mockResolvedValue({ data: { ...payload("salla"), provider:"direct", reports:[{
        provider:"direct", label:"طلبات مباشرة", status:"available", totals:metrics,
        rows:[{...row, provider:"direct", row_kind:"order_source", name:"طلبات مباشرة"}]}] } });
    await act(async () => root.render(<MemoryRouter initialEntries={["/ads-manager/reports?provider=direct&source=platform"]}><MarketingReports /></MemoryRouter>));
    await flush();
    expect(api.get.mock.calls.at(-1)[1].params.source).toBe("salla");
    expect(host.textContent).toContain("غير مصنّفة لمنصة");
    expect(host.textContent).not.toContain("عرض نتائج طلبات مباشرة");
    expect(host.textContent).not.toContain("إخفاء الحملات بلا نشاط");
    expect(host.querySelector("tbody").textContent).toContain("طلبات سلة");
});

test("all sources show six rows and the synced Salla reconciliation without claiming live verification", async () => {
    api.get.mockResolvedValue({data:{...payload("salla"),provider:"all",reports:["snapchat","tiktok","meta","google","direct","unclassified"].map(provider=>({provider,label:provider,status:"available",totals:metrics})),
        reconciliation:{orders:100,sales_sar:20000,order_delta:0,sales_delta_sar:0,scope:"store",included_statuses:[]}}});
    await act(async () => root.render(<MemoryRouter initialEntries={["/ads-manager/reports?provider=all&source=salla"]}><MarketingReports /></MemoryRouter>));
    await flush();
    expect(host.querySelectorAll("tbody tr")).toHaveLength(6);
    const proof=host.querySelector('[data-testid="salla-reconciliation"]');
    expect(proof.textContent).toContain("100 طلب");
    expect(proof.textContent).toContain("فرق توزيع الطلبات: 0");
    expect(proof.textContent).toContain("المتزامنة");
    expect(host.textContent).toContain("Asia/Riyadh");
    expect(api.get.mock.calls.at(-1)[1].params.status_scope).toBe("all");
});
