import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { AuthProvider, useAuth } from "../../context/AuthContext";
import GoogleCommercialPlan from "./GoogleCommercialPlan";
import fixture from "../../services/__fixtures__/googleCommercialPlanPriorContext.json";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() }, formatApiErrorDetail: jest.fn() }));
const copy = (value) => JSON.parse(JSON.stringify(value));
const path = "/integrations-v2/google_ads/commercial-plans/seasonal-focus";
let container, root, actor, saved, priorRead, planRead;
function IdentityRefresh() {
    const { refreshUser } = useAuth();
    return <button type="button" aria-label="refresh-auth" onClick={() => refreshUser().catch(() => {})}>refresh</button>;
}
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    actor = { id: "owner", role: "owner" }; saved = copy(fixture.plan); priorRead = null; planRead = null;
    api.get.mockReset(); api.put.mockReset(); api.post.mockReset();
    api.get.mockImplementation((url) => {
        if (url === "/auth/me") return Promise.resolve({ data: copy(actor) });
        if (url === path) return planRead ? planRead() : Promise.resolve({ data: copy(saved) });
        if (url === path + "/prior-season-observations") return priorRead ? priorRead() : Promise.resolve({ data: copy(fixture.response) });
        return Promise.reject(new Error("unexpected request: " + url));
    });
    api.put.mockResolvedValue({ data: copy(fixture.unrelated_plan) });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) {
    await act(async () => {
        root.render(<AuthProvider><IdentityRefresh /><GoogleCommercialPlan isOwner {...props} /></AuthProvider>);
        await Promise.resolve(); await Promise.resolve();
    });
}
const element = (label) => container.querySelector('[aria-label="' + label + '"]');
const button = (text) => [...container.querySelectorAll("button")].find((node) => node.textContent === text);
async function click(node) { await act(async () => node.click()); }
async function input(label, value) {
    const node = element(label), proto = node.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => { Object.getOwnPropertyDescriptor(proto, "value").set.call(node, value); node.dispatchEvent(new Event("input", { bubbles: true })); });
}
async function save() {
    await click(element("راجعت حقول خطة الموسم"));
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}
const priorCalls = () => api.get.mock.calls.filter(([url]) => url.endsWith("/prior-season-observations"));
const evidence = () => element("مراجعة مشاهدات الموسم السابق");
const read = () => click(button("قراءة مشاهدات الفترة المحفوظة"));

test("real AuthProvider owner creates explicit prior context with empty optional reference serialized null then reads saved HTTP fixture", async () => {
    saved = copy(fixture.initial_plan); api.put.mockResolvedValue({ data: copy(fixture.plan) });
    await render();
    expect(api.get.mock.calls[0][0]).toBe("/auth/me");
    expect(element("إضافة فترة موسم سابق للمراجعة").checked).toBe(false);
    expect(priorCalls()).toHaveLength(0);
    await click(element("إضافة فترة موسم سابق للمراجعة"));
    expect(element("بداية الفترة السابقة").value).toBe("");
    expect(element("نهاية الفترة السابقة").value).toBe("");
    await input("بداية الفترة السابقة", "2025-11-01"); await input("نهاية الفترة السابقة", "2025-12-31");
    await input("وصف الموسم السابق", fixture.plan.fields.prior_season_context.season_label);
    await input("سبب اختيار الفترة السابقة", fixture.plan.fields.prior_season_context.alignment_note);
    await click(element("راجعت حقول خطة الموسم")); await input("مرجع تقويمي اختياري", "   ");
    expect(element("راجعت حقول خطة الموسم").checked).toBe(false);
    await save();
    expect(api.put).toHaveBeenCalledWith(path, { ...fixture.plan.fields, expected_revision: 3, acknowledge_review: true }, expect.any(Object));
    expect(priorCalls()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
    await read();
    expect(priorCalls()[0][1].params).toEqual({ expected_revision: 4 });
    expect(evidence().textContent).toContain("عملة الطلب: SAR");
    expect(evidence().textContent).toContain("لا نحسب النمو أو الربحية");
    expect(fixture.plan.fields).not.toHaveProperty("acknowledge_review");
    expect(fixture.plan.fields).not.toHaveProperty("expected_revision");
});

test("unrelated edit retains all five prior fields through actual writer-shaped response", async () => {
    await render(); await input("توجيه المالك", fixture.unrelated_plan.fields.instruction); await save();
    expect(api.put.mock.calls[0][1].prior_season_context).toEqual(fixture.plan.fields.prior_season_context);
    expect(api.put.mock.calls[0][1].expected_revision).toBe(4);
    expect(element("بداية الفترة السابقة").value).toBe("2025-11-01");
    expect(element("مرجع تقويمي اختياري").value).toBe("");
});

test("owner optional calendar reference is trimmed and retained without calendar verification", async () => {
    saved = copy(fixture.removed_plan); api.put.mockResolvedValue({ data: copy(fixture.reference_plan) });
    await render(); await click(element("إضافة فترة موسم سابق للمراجعة"));
    const prior = fixture.reference_plan.fields.prior_season_context;
    await input("بداية الفترة السابقة", prior.date_from); await input("نهاية الفترة السابقة", prior.date_to);
    await input("وصف الموسم السابق", prior.season_label); await input("سبب اختيار الفترة السابقة", prior.alignment_note);
    await input("مرجع تقويمي اختياري", "  " + prior.calendar_reference_text + "  "); await save();
    expect(api.put.mock.calls[0][1]).toEqual({ ...fixture.reference_plan.fields, expected_revision: 6, acknowledge_review: true });
    expect(element("مرجع تقويمي اختياري").value).toBe(prior.calendar_reference_text);
    expect(fixture.reference_plan.review_context.prior_season.alignment_verification).toBe("unknown");
});

test("explicit removal sends null and removes prior child until another context is saved", async () => {
    saved = copy(fixture.unrelated_plan); api.put.mockResolvedValue({ data: copy(fixture.removed_plan) });
    await render(); await click(element("راجعت حقول خطة الموسم")); await click(button("إزالة فترة الموسم السابق"));
    expect(element("راجعت حقول خطة الموسم").checked).toBe(false);
    expect(element("بداية الفترة السابقة")).toBeNull(); expect(evidence()).toBeNull();
    await save(); expect(api.put.mock.calls[0][1].prior_season_context).toBeNull();
    expect(api.put.mock.calls[0][1].expected_revision).toBe(5); expect(evidence()).toBeNull();
    expect(fixture.removed_response.status).toBe("prior_season_not_configured");
    expect(priorCalls()).toHaveLength(0);
});

test.each([
    ["بداية الفترة السابقة", "2026-01-01"], ["نهاية الفترة السابقة", "2026-11-01"],
    ["بداية الفترة السابقة", "2024-01-01"], ["بداية الفترة السابقة", "2025-02-30"],
    ["سبب اختيار الفترة السابقة", "   "],
])("invalid prior %s=%s cannot PUT and remains editable", async (label, value) => {
    await render(); await input(label, value); await save();
    expect(api.put).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(element("بداية الفترة السابقة").disabled).toBe(false);
    expect(element("راجعت حقول خطة الموسم").checked).toBe(false);
});

test("prior response identity must match authenticated user rather than saved plan updated_by", async () => {
    saved.updated_by = "different-owner";
    const response = copy(fixture.response); response.owner_review_provenance.reviewed_by = "different-owner";
    priorRead = () => Promise.resolve({ data: response });
    await render(); await read();
    expect(evidence().querySelector('[role="alert"]')).not.toBeNull();
    expect(evidence().textContent).not.toContain("عملة الطلب: SAR");
});

test.each(["prior", "unrelated", "remove", "reload", "owner", "role", "unavailable", "unmount"])("actual parent %s change aborts and discards late prior result", async (kind) => {
    let finish; priorRead = () => new Promise((resolve) => { finish = resolve; });
    await render(); await read(); const signal = priorCalls()[0][1].signal;
    if (kind === "prior") await input("سبب اختيار الفترة السابقة", "سبب جديد");
    if (kind === "unrelated") await input("توجيه المالك", "توجيه جديد");
    if (kind === "remove") await click(button("إزالة فترة الموسم السابق"));
    if (kind === "reload") { saved.fields.message = "رسالة أخرى"; await click(button("إعادة تحميل خطة الموسم")); }
    if (kind === "owner") { actor.id = "another-owner"; await click(element("refresh-auth")); }
    if (kind === "role") { actor.role = "viewer"; await click(element("refresh-auth")); }
    if (kind === "unavailable") { api.get.mockRejectedValueOnce({ response: { status: 503 } }); await click(element("refresh-auth")); }
    if (kind === "unmount") await act(async () => root.render(null));
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: copy(fixture.response) }));
    expect(container.textContent).not.toContain("عملة الطلب: SAR");
});

test.each(["load", "save"])("authenticated owner switch discards late plan %s response", async (kind) => {
    let finish;
    if (kind === "load") planRead = () => new Promise((resolve) => { finish = resolve; });
    else api.put.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render();
    if (kind === "save") { await input("توجيه المالك", "تعديل المالك الأول"); await save(); }
    const call = kind === "load" ? api.get.mock.calls.find(([url]) => url === path) : api.put.mock.calls[0];
    const signal = call[kind === "load" ? 1 : 2].signal;
    actor.id = "new-owner"; saved.fields.instruction = "خطة المالك الحالي"; planRead = null;
    await click(element("refresh-auth")); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: copy(fixture.plan) }));
    expect(element("توجيه المالك").value).toBe("خطة المالك الحالي");
    expect(container.textContent).not.toContain("حُفظت خطة الموسم للمراجعة التجارية.");
});

test("non-owner permission cannot load the plan even with an authenticated owner context", async () => {
    await render({ isOwner: false });
    expect(api.get.mock.calls.map(([url]) => url)).toEqual(["/auth/me"]);
    expect(container.querySelector("form")).toBeNull(); expect(api.put).not.toHaveBeenCalled();
});
