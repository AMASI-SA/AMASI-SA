import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import api from "../../lib/api";
import GoogleAdsWorkspace, { GoogleCapabilityMatrix } from "./GoogleAdsWorkspace";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const account = { account_id: "123", account_name: "أماسي", currency: "SAR", timezone: "Asia/Riyadh" };
const readiness = { accounts: [account], management_enabled: true, permissions: { can_draft: true, can_manage: true, can_execute: true, owner: false }, campaign_types: [{ type: "SEARCH", create: true, manage: true, implemented: "builder_ready", formats: ["RSA"] }, { type: "PERFORMANCE_MAX", create: true, manage: true, implemented: "builder_ready", actions: ["rename", "pause", "enable", "add_group", "update_group_assets", "rename_group", "remove_group"] }, { type: "VIDEO", create: false, implemented: "creation_unavailable", provider_limitation: "إنشاء الفيديو التقليدي غير متاح." }] };
const campaign = { entity_id: "456", account_id: "123", account_name: "أماسي", name: "حملة منتجات أماسي", campaign_type: "PERFORMANCE_MAX", status: "ENABLED", currency: "SAR", spend_native: "110", conversions: 4, impressions: 3000, clicks: 40 };
let container, root;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/readiness") ? readiness : url.endsWith("/workspace") ? { accounts: [account], items: [campaign], pagination: { page: 1, pages: 1, total: 1 } } : { items: [] } }));
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (name) => [...container.querySelectorAll("button")].find((element) => element.textContent === name);
async function render(props = {}) { await act(async () => root.render(<GoogleAdsWorkspace dateFrom="2026-10-01" dateTo="2026-10-13" initialAccountId="123" {...props} />)); }

test("PMax navigation follows asset groups and assets rather than ordinary ad groups", async () => {
    api.get.mockImplementation((url, options) => Promise.resolve({ data: url.endsWith("/readiness") ? readiness : url.endsWith("/workspace") ? { accounts: [account], items: options.params.entity_type === "campaigns" ? [campaign] : options.params.entity_type === "groups" ? [{ ...campaign, entity_id: "789", group_id: "789", campaign_id: "456", name: "مواد الهدايا", entity_type: "asset_group" }] : [{ resource_name: "customers/123/assets/9", name: "شعار أماسي", type: "IMAGE", account_id: "123" }], pagination: { page: 1, pages: 1, total: 1 } } : { items: [] } }));
    await render(); await act(async () => button("عرض مجموعات المواد").click());
    expect(api.get.mock.calls.find(([url, options]) => url.endsWith("/workspace") && options.params.entity_type === "groups")[1].params).toMatchObject({ account_id: "123", campaign_id: "456" });
    expect(container.textContent).toContain("مواد الهدايا"); await act(async () => button("عرض المواد").click());
    const lastRead = api.get.mock.calls.filter(([url]) => url.endsWith("/workspace")).at(-1);
    expect(lastRead[1].params).toMatchObject({ entity_type: "assets", campaign_id: "456", group_id: "789" }); expect(container.textContent).toContain("شعار أماسي"); expect(api.post).not.toHaveBeenCalled();
});

test("changing a date range aborts and discards its older campaign response", async () => {
    let finish;
    api.get.mockImplementation((url, options) => url.endsWith("/workspace") && options.params.to_date === "2026-10-12" ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve({ data: url.endsWith("/readiness") ? readiness : url.endsWith("/workspace") ? { accounts: [account], items: [{ ...campaign, name: "بيانات اليوم" }] } : { items: [] } }));
    await render({ dateTo: "2026-10-12" }); const previous = api.get.mock.calls.find(([url]) => url.endsWith("/workspace"));
    await render({ dateTo: "2026-10-13" }); expect(previous[1].signal.aborted).toBe(true);
    await act(async () => finish({ data: { accounts: [account], items: [{ ...campaign, name: "بيانات قديمة" }] } }));
    expect(container.textContent).toContain("بيانات اليوم"); expect(container.textContent).not.toContain("بيانات قديمة");
});

test("draft history reopens a scoped persisted draft without any provider write", async () => {
    const draft = { account_id: "123", campaign_type: "SEARCH", name: "مسودة هدية", budget_native: "99.50", budget_mode: "DAILY", bidding: { strategy: "MAXIMIZE_CONVERSIONS" }, groups: [], geo_target_ids: [], language_ids: [] };
    const record = { draft_id: "d_1", revision: 3, draft };
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/readiness") ? readiness : url.endsWith("/workspace") ? { accounts: [account], items: [campaign] } : url.endsWith("/drafts") ? { items: [record] } : url.endsWith("/drafts/d_1") ? record : { items: [] } }));
    await render(); await act(async () => button("المسودات المحفوظة").click());
    await act(async () => [...container.querySelectorAll('section[aria-label="مسودات جوجل المحفوظة"] button')].find((element) => element.textContent.includes("مسودة هدية")).click());
    expect(container.querySelector('[aria-label="اسم حملة جوجل"]').value).toBe("مسودة هدية"); expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});

test("capability map distinguishes implementation proof, API restrictions and account prerequisites", () => {
    const markup = renderToStaticMarkup(<GoogleCapabilityMatrix readiness={{ campaign_types: [...readiness.campaign_types, { type: "SHOPPING", create: true, implemented: "builder_ready", account_eligible: false, missing_prerequisites: ["linked_merchant_center"] }] }} accountId="123" />);
    expect(markup).toContain("قابل للاختبار"); expect(markup).toContain("إنشاء حملات فيديو YouTube التقليدية غير متاح"); expect(markup).toContain("متطلبات ناقصة"); expect(markup).toContain("ربط Merchant Center"); expect(markup).not.toContain("مكتمل 100%");
});

test("Google AI recommendation opens the governed budget editor with the exact native amount", async () => {
    api.post.mockResolvedValue({ data: { summary: "تحليل الفترة", recommendations: [{ campaign_id: "456", account_id: "123", campaign_name: campaign.name, action: "set_budget", title: "ضبط الميزانية", rationale: "اختبار توسع تدريجي للحملة", proposed_budget_native: "123.45" }], limitations: ["جودة الإسناد تحتاج مراجعة"] } });
    await render(); await act(async () => button("تحليل الحساب بالذكاء").click());
    expect(container.textContent).toContain("اختبار توسع تدريجي للحملة"); await act(async () => button("مراجعة التحسين المقترح").click());
    expect(container.querySelector('[aria-label="ميزانية إدارة جوجل"]').value).toBe("123.45");
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.post.mock.calls[0][0]).toContain("/ai/analyze");
});

test("owner selects an existing employee and saves a per-account delegation", async () => {
    const ownerReadiness = { ...readiness, permissions: { ...readiness.permissions, owner: true } };
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/readiness") ? ownerReadiness : url.endsWith("/workspace") ? { accounts: [account], items: [] } : url.endsWith("/subjects") ? { items: [{ subject_id: "staff_1", name: "المسوّق" }] } : url.endsWith("/policies/123/staff_1") ? { enabled: false, allowed_actions: [], max_daily_budget_native: "100", max_total_budget_native: "1000" } : { items: [] } }));
    api.put.mockResolvedValue({ data: { enabled: true } });
    await render(); const select = container.querySelector('[aria-label="موظف تفويض جوجل"]');
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, "staff_1"); select.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => button("حفظ تفويض جوجل").closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(api.put.mock.calls[0][0]).toBe("/integrations-v2/google_ads/management/policies/123/staff_1"); expect(api.put.mock.calls[0][1]).toMatchObject({ max_daily_budget_native: "100", max_total_budget_native: "1000" });
});
