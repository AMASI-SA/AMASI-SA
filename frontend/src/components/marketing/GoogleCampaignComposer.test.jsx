import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleCampaignComposer, { googleDefaultDraft } from "./GoogleCampaignComposer";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const readiness = { management_enabled: true, permissions: { can_draft: true, can_manage: true, can_execute: true }, campaign_types: [{ type: "SEARCH", create: true, manage: true, implemented: "builder_ready", formats: ["RSA", "DSA"], bidding_strategies: ["MAXIMIZE_CONVERSIONS", "MANUAL_CPC"], budget_modes: ["DAILY", "TOTAL"] }, { type: "VIDEO", create: false, implemented: "creation_unavailable", provider_limitation: "إنشاء فيديو تقليدي غير متاح عبر جوجل." }] };
const draft = { ...googleDefaultDraft("123"), name: "هدية أماسي", budget_native: "139.50", groups: [{ ...googleDefaultDraft("123").groups[0], headlines: ["هدية باسمها", "أماسي للهدايا", "توصيل للسعودية"], descriptions: ["هدية بتصميم خاص", "اختاري الاسم واللون"], final_urls: ["https://mezansalla.com/gift"] }] };
const selection = { mode: "create", accountId: "123", accountName: "أماسي", currency: "SAR", initialDraft: draft };
const preview = { proposal_id: "p_1", action: "create_campaign", status: "previewed", confirmation_digest: "digest_1", account_id: "123", currency: "SAR", planned: { name: draft.name, budget_native: draft.budget_native }, before: {} };
let container, root;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
    api.get.mockResolvedValue({ data: { items: [] } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (name) => [...container.querySelectorAll("button")].find((element) => element.textContent === name);
async function change(label, value) {
    const element = container.querySelector(`[aria-label="${label}"]`);
    const prototype = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : element.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    await act(async () => { Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value); element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true })); });
}
async function render(props = {}) { await act(async () => root.render(<GoogleCampaignComposer selection={selection} readiness={readiness} {...props} />)); }
async function submit() { await change("سبب عملية جوجل", "تجربة منتج أماسي"); await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }

test("saves and reopens a real editable campaign draft with all creative text", async () => {
    api.post.mockImplementation((url, body) => Promise.resolve({ data: { draft_id: "d_1", revision: 1, draft: body.draft } }));
    await render(); expect(button("معاينة العملية في جوجل").disabled).toBe(true);
    await act(async () => button("حفظ المسودة").click());
    const saved = api.post.mock.calls[0][1].draft;
    expect(saved.budget_native).toBe("139.50"); expect(saved.groups[0].headlines).toEqual(draft.groups[0].headlines);
    await act(async () => root.render(<div />));
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/drafts/d_1") ? { draft_id: "d_1", revision: 1, draft: saved } : { items: [] } }));
    api.put.mockResolvedValue({ data: { draft_id: "d_1", revision: 2, draft: { ...saved, budget_native: "149.50" } } });
    await render({ selection: { ...selection, initialDraft: undefined, draftId: "d_1" } });
    expect(container.querySelector('[aria-label="اسم حملة جوجل"]').value).toBe("هدية أماسي"); expect(container.querySelector('[aria-label="عناوين المجموعة 1"]').value).toContain("هدية باسمها");
    await change("ميزانية حملة جوجل", "149.50"); expect(button("معاينة العملية في جوجل").disabled).toBe(true);
    await act(async () => button("حفظ تعديلات المسودة").click());
    expect(api.put.mock.calls[0][1]).toMatchObject({ expected_revision: 1, draft: { budget_native: "149.50" } });
});

test("unsupported type is visible, disabled and cannot prepare a creation", async () => {
    await render(); const video = [...container.querySelector('[aria-label="نوع حملة جوجل"]').options].find((option) => option.value === "VIDEO");
    expect(video.disabled).toBe(true); expect(video.textContent).toContain("غير متاح"); expect(api.post).not.toHaveBeenCalled();
});

test("PMax draft saves its complete selected assets and search themes unchanged", async () => {
    const pmaxDraft = { ...googleDefaultDraft("123", "PERFORMANCE_MAX"), name: "هدايا أماسي", budget_native: "200", groups: [{ ...googleDefaultDraft("123", "PERFORMANCE_MAX").groups[0], headlines: ["هدية بالاسم", "هدية سعودية", "أماسي"], long_headlines: ["هديتك بتصميم خاص باسمها"], descriptions: ["هدية من القلب", "تصميم خاص"], final_urls: ["https://mezansalla.com/gift"], business_name: "أماسي", image_assets: ["customers/123/assets/10"], square_image_assets: ["customers/123/assets/11"], logo_assets: ["customers/123/assets/12"], search_themes: ["هدايا باسمها"] }] };
    const pmaxReadiness = { ...readiness, campaign_types: [...readiness.campaign_types, { type: "PERFORMANCE_MAX", create: true, manage: true, formats: ["MULTI_ASSET"], implemented: "builder_ready" }] };
    api.post.mockImplementation((url, body) => Promise.resolve({ data: { draft_id: "pmax_draft", revision: 1, draft: body.draft } }));
    await render({ selection: { ...selection, initialDraft: pmaxDraft }, readiness: pmaxReadiness });
    await act(async () => button("حفظ المسودة").click());
    expect(api.post.mock.calls[0][1].draft.groups[0]).toMatchObject({ image_assets: ["customers/123/assets/10"], square_image_assets: ["customers/123/assets/11"], logo_assets: ["customers/123/assets/12"], search_themes: ["هدايا باسمها"] });
});

test("preview binds saved revision and a changed server digest clears approval", async () => {
    api.post.mockImplementation((url, body) => Promise.resolve({ data: url.endsWith("/drafts") ? { draft_id: "d_1", revision: 4, draft: body.draft } : preview }));
    await render(); await act(async () => button("حفظ المسودة").click()); await submit();
    expect(api.post.mock.calls[1][1]).toMatchObject({ action: "create", draft_id: "d_1", draft_revision: 4 });
    expect(button("اعتماد وتنفيذ في جوجل").disabled).toBe(true);
    await act(async () => container.querySelector('[aria-label="اعتماد معاينة جوجل المحددة"]').click()); expect(button("اعتماد وتنفيذ في جوجل").disabled).toBe(false);
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/proposals/p_1") ? { ...preview, confirmation_digest: "digest_2" } : { items: [] } }));
    await act(async () => button("تحديث حالة العملية").click()); expect(button("اعتماد وتنفيذ في جوجل").disabled).toBe(true);
    expect(container.querySelector('[aria-label="اعتماد معاينة جوجل المحددة"]').checked).toBe(false);
});

test("lost execution response blocks double writes and allows read-only reconciliation", async () => {
    const complete = jest.fn(); api.post.mockRejectedValueOnce(new Error("network timeout"));
    await render({ selection: { ...selection, proposal: preview }, onCompleted: complete });
    await act(async () => container.querySelector('[aria-label="اعتماد معاينة جوجل المحددة"]').click());
    await act(async () => { const execute = button("اعتماد وتنفيذ في جوجل"); execute.click(); execute.click(); });
    expect(api.post).toHaveBeenCalledTimes(1); expect(button("اعتماد وتنفيذ في جوجل")).toBeUndefined();
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/proposals/p_1") ? { ...preview, status: "outcome_unknown" } : { items: [] } }));
    await act(async () => button("تحديث حالة العملية").click()); expect(api.post).toHaveBeenCalledTimes(1);
    api.post.mockResolvedValueOnce({ data: { ...preview, status: "completed", verified: true, after: { name: draft.name }, created_campaign_id: "456" } });
    await act(async () => button("التحقق من النتيجة في جوجل").click()); expect(api.post.mock.calls[1][0]).toContain("/reconcile"); expect(complete).toHaveBeenCalledTimes(1);
});

test("image upload stays a reviewed proposal until independent verification", async () => {
    const assetPreview = { ...preview, action: "upload_asset", proposal_id: "asset_1", planned: { type: "IMAGE", name: "gift.png" } };
    const completed = jest.fn(); api.post.mockResolvedValueOnce({ data: assetPreview });
    await render({ onCompleted: completed });
    const fileInput = container.querySelector('[aria-label="رفع صورة جوجل"]'); const file = new File(["png"], "gift.png", { type: "image/png" });
    await act(async () => { Object.defineProperty(fileInput, "files", { configurable: true, value: [file] }); fileInput.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => button("معاينة إضافة الصورة").click()); expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/assets"); expect(completed).not.toHaveBeenCalled();
    expect(button("اعتماد وتنفيذ في جوجل").disabled).toBe(true); expect(container.textContent).not.toContain("تأكدت الإعدادات بقراءة مستقلة");
    api.post.mockResolvedValueOnce({ data: { ...assetPreview, status: "completed", verified: true, after: { resource_name: "customers/123/assets/9", type: "IMAGE", name: "gift.png" } } });
    await act(async () => container.querySelector('[aria-label="اعتماد معاينة جوجل المحددة"]').click()); await act(async () => button("اعتماد وتنفيذ في جوجل").click());
    expect(completed).toHaveBeenCalledTimes(1); expect(button("العودة للمسودة واختيار المادة")).toBeDefined();
});

test("HTML5 ZIP is approved as an opaque material and selectable only after verification", async () => {
    const html5Preview = { ...preview, action: "upload_html5", proposal_id: "html5_1", planned: { type: "MEDIA_BUNDLE", name: "display-ad.zip" } };
    const asset = { resource_name: "customers/123/assets/19", type: "MEDIA_BUNDLE", name: "display-ad.zip" };
    let verified = false;
    api.get.mockImplementation((url) => Promise.resolve({ data: { items: url.endsWith("/assets") && verified ? [asset] : [] } }));
    api.post.mockResolvedValueOnce({ data: html5Preview }).mockImplementationOnce(() => { verified = true; return Promise.resolve({ data: { ...html5Preview, status: "completed", verified: true, after: asset } }); });
    const html5Readiness = { ...readiness, campaign_types: [...readiness.campaign_types, { type: "DISPLAY", create: true, manage: true, formats: ["RESPONSIVE_DISPLAY", "UPLOADED_HTML5"], implemented: "builder_ready" }] };
    const html5Draft = { ...googleDefaultDraft("123", "DISPLAY"), name: "حملة HTML5", budget_native: "100", groups: [{ ...googleDefaultDraft("123", "DISPLAY").groups[0], ad_format: "UPLOADED_HTML5", final_urls: ["https://mezansalla.com/gift"] }] };
    await render({ selection: { ...selection, initialDraft: html5Draft }, readiness: html5Readiness });
    const input = container.querySelector('[aria-label="رفع إعلان HTML5 جوجل"]'); const file = new File(["opaque archive"], "display-ad.zip", { type: "application/zip" });
    await act(async () => { Object.defineProperty(input, "files", { configurable: true, value: [file] }); input.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => button("معاينة إضافة إعلان HTML5").click());
    expect(api.post.mock.calls[0][1].get("asset_type")).toBe("HTML5"); expect(button("اعتماد وتنفيذ في جوجل").disabled).toBe(true);
    expect(container.querySelectorAll("iframe,object,embed")).toHaveLength(0); expect(container.textContent).not.toContain("تأكدت الإعدادات بقراءة مستقلة");
    await act(async () => container.querySelector('[aria-label="اعتماد معاينة جوجل المحددة"]').click()); await act(async () => button("اعتماد وتنفيذ في جوجل").click());
    await act(async () => button("العودة للمسودة واختيار المادة").click());
    const picker = container.querySelector('[aria-label="مادة HTML5 جوجل 1"]');
    expect([...picker.options].some((option) => option.value === "customers/123/assets/19" && option.textContent === "display-ad.zip")).toBe(true);
    expect(api.post.mock.calls[1][0]).toContain("/proposals/html5_1/approve-and-execute"); expect(container.querySelectorAll("iframe,object,embed")).toHaveLength(0);
});

test("HTML5 file-size validation prevents an oversized archive reaching the proposal endpoint", async () => {
    await render();
    const input = container.querySelector('[aria-label="رفع إعلان HTML5 جوجل"]'); const file = new File(["opaque archive"], "too-large.zip", { type: "application/zip" });
    Object.defineProperty(file, "size", { value: 5 * 1024 * 1024 + 1 });
    await act(async () => { Object.defineProperty(input, "files", { configurable: true, value: [file] }); input.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(button("معاينة إضافة إعلان HTML5").disabled).toBe(true); expect(container.textContent).toContain("حجم ملف HTML5 يجب ألا يتجاوز 5 ميغابايت"); expect(api.post).not.toHaveBeenCalled();
});

test("a late draft from a prior editor cannot overwrite a newly selected campaign", async () => {
    let finish;
    api.get.mockImplementation((url) => url.endsWith("/drafts/old_draft") ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve({ data: { items: [] } }));
    await render({ selection: { ...selection, initialDraft: undefined, draftId: "old_draft" } });
    const signal = api.get.mock.calls.find(([url]) => url.endsWith("/drafts/old_draft"))[1].signal;
    await act(async () => root.render(<GoogleCampaignComposer key="new" selection={{ ...selection, initialDraft: { ...draft, name: "الحملة الجديدة" } }} readiness={readiness} />));
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: { draft_id: "old_draft", revision: 2, draft: { ...draft, name: "المسودة القديمة" } } }));
    expect(container.querySelector('[aria-label="اسم حملة جوجل"]').value).toBe("الحملة الجديدة");
});

test("entity group status changes submit the trusted selection without editable resource IDs", async () => {
    api.post.mockResolvedValue({ data: { ...preview, action: "pause_group", planned: { status: "PAUSED" } } });
    await render({ selection: { mode: "edit", action: "pause_group", accountId: "123", campaignId: "456", groupId: "789", campaignType: "SEARCH", name: "المجموعة" } });
    await submit(); expect(api.post.mock.calls[0][1]).toMatchObject({ action: "pause_group", campaign_id: "456", changes: { group_id: "789" } });
    expect(container.querySelector('textarea[aria-label*="JSON"]')).toBeNull(); expect(container.querySelector('input[aria-label*="معرف"]')).toBeNull();
});

test("named geo and language choices persist digit IDs even when the lookup also supplies resource names", async () => {
    jest.useFakeTimers();
    try {
        api.get.mockImplementation((url) => Promise.resolve({ data: { items: url.endsWith("/lookup/languages") ? [{ id: "1000", resource_name: "languageConstants/1000", name: "العربية", code: "ar" }] : url.endsWith("/lookup/geo") ? [{ id: "2682", resource_name: "geoTargetConstants/2682", name: "السعودية" }] : [] } }));
        api.post.mockImplementation((url, body) => Promise.resolve({ data: { draft_id: "scoped_draft", revision: 1, draft: body.draft } }));
        await render();
        await change("بحث مواقع جوجل", "السعودية");
        await act(async () => { jest.advanceTimersByTime(300); });
        await act(async () => container.querySelector('[aria-label="المواقع الجغرافية: السعودية"]').click());
        await act(async () => container.querySelector('[aria-label="لغات العملاء: العربية"]').click());
        await act(async () => button("حفظ المسودة").click());
        expect(api.post.mock.calls[0][1].draft.geo_target_ids).toEqual(["2682"]);
        expect(api.post.mock.calls[0][1].draft.language_ids).toEqual(["1000"]);
    } finally { jest.useRealTimers(); }
});
