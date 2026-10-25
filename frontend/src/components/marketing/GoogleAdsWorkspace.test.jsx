import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import api from "../../lib/api";
import GoogleAdsWorkspace, { GoogleCapabilityMatrix, googleCreativeEditAllowed, googleTypedCampaignContext } from "./GoogleAdsWorkspace";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
beforeAll(() => { Object.defineProperty(globalThis, "crypto", { configurable: true, value: require("crypto").webcrypto }); });
afterAll(() => { if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto); else delete globalThis.crypto; });
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

test("Google's saved intelligence card is read in its native page with the selected account and period", async () => {
    await render();
    expect(container.querySelector('[data-testid="google-ads-intelligence-card"]')).not.toBeNull();
    const request = api.get.mock.calls.find(([url]) => url.endsWith("/ai/analyses"));
    expect(request[1].params).toMatchObject({ account_id: "123", date_from: "2026-10-01", date_to: "2026-10-13" });
    expect(api.post).not.toHaveBeenCalled();
});

test("one provider-readable owned account is selected instead of an inaccessible first link", async () => {
    const missing = { account_id: "999", account_name: "حساب غير متاح", readable: false };
    const readable = { ...account, readable: true };
    const onAccountChange = jest.fn();
    api.get.mockImplementation((url, options) => Promise.resolve({ data: url.endsWith("/readiness")
        ? { ...readiness, accounts: [missing, readable] }
        : url.endsWith("/workspace") ? { accounts: [missing, readable], items: options.params.account_id === "123" ? [campaign] : [], account_selection_required: !options.params.account_id }
        : { items: [] } }));
    await render({ initialAccountId: "", onAccountChange });
    expect(container.querySelector('[aria-label="حساب إعلانات جوجل"]').value).toBe("123");
    expect(onAccountChange).toHaveBeenCalledWith("123");
    expect(api.get.mock.calls.some(([url, options]) => url.endsWith("/workspace") && options.params.account_id === "123")).toBe(true);
    expect(api.get.mock.calls.some(([url, options]) => url.endsWith("/workspace") && options.params.account_id === "999")).toBe(false);
    expect(container.textContent).toContain(campaign.name);
});

test("an explicit report-account change clears foreign editor and hierarchy state", async () => {
    const second = { ...account, account_id: "222", account_name: "الحساب الثاني" };
    api.get.mockImplementation((url, options) => Promise.resolve({ data: url.endsWith("/readiness") ? { ...readiness, accounts: [account, second] }
        : url.endsWith("/workspace") ? { accounts: [account, second], items: options.params.account_id === "123" ? [campaign] : [] }
        : { items: [] } }));
    await render({ selectedAccountId: "123" });
    await act(async () => button("إنشاء حملة جوجل").click());
    expect(container.querySelector('[data-testid="google-campaign-composer"]')).not.toBeNull();
    await render({ selectedAccountId: "222" });
    expect(container.querySelector('[aria-label="حساب إعلانات جوجل"]').value).toBe("222");
    expect(container.querySelector('[data-testid="google-campaign-composer"]')).toBeNull();
    const last = api.get.mock.calls.filter(([url]) => url.endsWith("/workspace")).at(-1);
    expect(last[1].params).toMatchObject({ account_id: "222", entity_type: "campaigns" });
});

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

test("native AI advice reaches frozen review and executes only after explicit approval", async () => {
    const advice = { analysis_id: "saved-analysis", summary: "تحليل الفترة", recommendations: [{
        recommendation_id: "google_ads:campaign:123:456:PAUSE_CAMPAIGN", campaign_id: "456", account_id: "123",
        campaign_name: campaign.name, action: "PAUSE_CAMPAIGN", title: "مراجعة إيقاف الحملة", confidence: "high",
        rationale: "اختبار قرار مسنود بأدلة جوجل",
    }] };
    const proposal = { proposal_id: "frozen-ai-proposal", action: "pause", account_id: "123", campaign_id: "456",
        currency: "SAR", status: "previewed", confirmation_digest: "a".repeat(64),
        before: { name: campaign.name, campaign_type: "PERFORMANCE_MAX", status: "ENABLED" },
        ai_source: { analysis_id: "saved-analysis", date_from: "2026-10-01", date_to: "2026-10-13" } };
    api.post.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/ai/analyze") ? advice
        : url.endsWith("/recommendations/preview") ? proposal : { ...proposal, status: "completed", verified: true } }));
    await render(); await act(async () => button("تحليل الحساب بالذكاء").click());
    expect(container.textContent).toContain("درجة الثقة: مرتفعة"); expect(container.textContent).not.toContain("NaN");
    await act(async () => button("مراجعة تنفيذ التوصية").click());
    expect(container.querySelector('[data-testid="google-proposal-review"]')).not.toBeNull();
    expect(api.post.mock.calls[1][1]).toEqual({ analysis_id: "saved-analysis", recommendation_id: advice.recommendations[0].recommendation_id });
    const approval = container.querySelector('[aria-label="اعتماد معاينة جوجل المحددة"]');
    expect(approval.checked).toBe(false); expect(button("اعتماد وتنفيذ في جوجل").disabled).toBe(true);
    expect(api.post).toHaveBeenCalledTimes(2);
    await act(async () => approval.click()); await act(async () => button("اعتماد وتنفيذ في جوجل").click());
    expect(api.post.mock.calls[2][0]).toContain("/frozen-ai-proposal/approve-and-execute");
    expect(api.post.mock.calls[2][1]).toEqual({ confirmation_digest: proposal.confirmation_digest });
    expect(container.textContent).toContain("تأكدت الإعدادات بقراءة مستقلة من جوجل");
});

test("native AI evidence keeps counts and ratios separate from account-currency money", async () => {
    const evidence = [
        ["impressions", "2298"], ["clicks", 101], ["conversions", "4.5"],
        ["spend_native", "41.834257"], ["conversion_value_native", "1471.18"],
        ["cpc_native", "0.4142"], ["roas", "3.5"], ["ctr", "0.042"],
        ["ctr_pct", "4.2"], ["orders", 0], ["units", 6],
        ["net_profit_native", "-8"], ["future_metric", "12"],
        ["spend_native", null], ["ctr", ""],
    ].map(([metric, value]) => ({ metric, value, source: "google_ads_api", currency_code: "SAR" }));
    api.post.mockResolvedValue({ data: { summary: "تحليل الوحدات", recommendations: [{
        campaign_id: "456", account_id: "123", title: "أدلة الأداء", action: "MONITOR", evidence,
    }] } });
    await render(); await act(async () => button("تحليل الحساب بالذكاء").click());
    const region = container.querySelector('[aria-label="تحليل جوجل بذكاء ميزان"]');
    const lines = [...region.querySelectorAll("p")].map((item) => item.textContent);
    for (const expected of ["الظهور: 2,298", "النقرات: 101", "التحويلات: 4.5", "الطلبات: 0", "الوحدات: 6",
        "الصرف: 41.83 SAR", "قيمة التحويلات: 1,471.18 SAR", "تكلفة النقرة: 0.41 SAR",
        "العائد على الإنفاق: 3.5×", "نسبة النقر: 4.2%", "صافي الربح: -8 SAR", "القيمة: 12", "الصرف: —", "نسبة النقر: —"]) {
        expect(lines).toContain(`google_ads_api · ${expected}`);
    }
    expect(region.textContent).not.toMatch(/(?:2,298|101|4\.5|3\.5×|4\.2%) SAR/);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toContain("/ai/analyze");
    expect(api.put).not.toHaveBeenCalled();
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

test("an incomplete PMax projection blocks material replacement and explains the field gaps", async () => {
    const incompleteGroup = { ...campaign, entity_id: "789", group_id: "789", campaign_id: "456", name: "مواد غير مكتملة", editable: false, creative_supported: false, missing_fields: ["headlines"], unsupported_fields: ["asset_group.external_asset"] };
    api.get.mockImplementation((url, options) => Promise.resolve({ data: url.endsWith("/readiness") ? readiness : url.endsWith("/workspace") ? { accounts: [account], items: options.params.entity_type === "campaigns" ? [campaign] : [incompleteGroup] } : { items: [] } }));
    await render(); await act(async () => button("عرض مجموعات المواد").click());
    expect(button("تعديل مواد المجموعة").disabled).toBe(true);
    expect(container.querySelector('[aria-label="فجوات قراءة مواد جوجل"]').textContent).toContain("عناوين الإعلان");
    expect(container.querySelector('[aria-label="فجوات قراءة مواد جوجل"]').textContent).toContain("مواد الإعلان");
    expect(api.post).not.toHaveBeenCalled();
});

test("creative read gaps block replacement while an authorized pause remains available", () => {
    expect(googleCreativeEditAllowed({ editable: false }, "update_ad")).toBe(false);
    expect(googleCreativeEditAllowed({ creative_supported: false }, "update_group_assets")).toBe(false);
    expect(googleCreativeEditAllowed({ creative_supported: false }, "pause_ad")).toBe(true);
    expect(googleCreativeEditAllowed({ creative_supported: true, editable: true }, "update_ad")).toBe(true);
});

test("App engagement context survives hierarchy navigation and ad creation", async () => {
    const appContext = { app: { subtype: "ENGAGEMENT", app_id: "com.amasi.app", app_store: "GOOGLE_APP_STORE", conversion_action_ids: ["customers/123/conversionActions/9"] } };
    const appCampaign = { ...campaign, campaign_type: "APP", name: "تفاعل تطبيق أماسي", campaign_context: appContext };
    const appReadiness = { ...readiness, campaign_types: [{ type: "APP", create: true, manage: true, actions: ["rename", "add_group", "add_ad", "rename_group"], formats: ["APP"], bidding_strategies: ["TARGET_CPA", "TARGET_ROAS"], implemented: "builder_ready" }] };
    api.get.mockImplementation((url, options) => Promise.resolve({ data: url.endsWith("/readiness") ? appReadiness : url.endsWith("/workspace") ? { accounts: [account], items: options.params.entity_type === "campaigns" ? [appCampaign] : [{ id: "789", account_id: "123", campaign_id: "456", name: "مستخدمو التطبيق", campaign_type: "APP", creative: { name: "مستخدمو التطبيق", ad_format: "APP", headlines: ["ارجعي للتطبيق"], descriptions: ["هدايا أماسي بالاسم"], final_urls: ["https://mezansalla.com/app/gift"] } }] } : { items: [] } }));
    await render(); await act(async () => button("عرض المجموعات").click());
    await act(async () => button("إضافة إعلان").click());
    expect(container.querySelector('[aria-label="روابط الوصول للمجموعة 1"]').value).toContain("https://mezansalla.com/app/gift");
    expect(container.textContent).toContain("رابط التطبيق الشامل");
    expect(container.textContent).not.toContain("وجهة الإعلان هي التطبيق المختار");
    expect(api.post).not.toHaveBeenCalled();
});

test("typed campaign context normalizes the install alias and preserves Merchant and Hotel links", () => {
    const context = googleTypedCampaignContext({ app: { subtype: "INSTALL", app_id: "com.amasi.app", app_store: "GOOGLE_APP_STORE" }, shopping: { merchant_id: "88", feed_label: "SA", campaign_priority: 1 }, hotel: { hotel_center_id: "99" } });
    expect(context.app.subtype).toBe("INSTALLS"); expect(context.shopping.merchant_id).toBe("88"); expect(context.hotel.hotel_center_id).toBe("99");
    expect(googleTypedCampaignContext({ app: { subtype: "ENGAGEMENT", app_id: "com.amasi.app" } }).app.subtype).toBe("ENGAGEMENT");
});

