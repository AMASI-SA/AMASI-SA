import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import api from "../lib/api";
import SallaWebhookRecovery, { SallaWebhookRecoveryLink } from "./SallaWebhookRecovery";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../context/AuthContext", () => ({ useAuth: jest.fn() }));

const API = "/salla/webhook-incident-recovery";
const START = "2026-11-15T13:57:00Z";
const END = "2026-11-15T14:45:00.000Z";
const HASH = "a".repeat(64);
const RUNTIME = { release_id: `rg5-${"b".repeat(64)}`, source_git_sha: "c".repeat(40) };
const config = () => ({
    enabled: true, runtime_validated: true, runtime: { ...RUNTIME }, incident_start: START,
    merchant_id: "1673234356", max_batch: 50, max_duration_seconds: 86400,
    queue: { counts: { pending: 0, retry: 0, processing: 0, quarantined: 5, done: 0 },
        grant_counts: { pending: 0, retry: 0, processing: 0, quarantined: 0, done: 0 },
        total_granted: 0, latest_completed_at: null },
});
const review = (request = { incident_end: END, batch_limit: 50 }, selected = 3, total = 5) => ({
    plan: {
        version: 1, incident_id: "salla-capture-contract-20261115",
        owner_id: "synthetic-owner", merchant_id: "1673234356", eligible_total: total,
        runtime: { ...RUNTIME }, incident_start: START, incident_end: request.incident_end,
        batch_limit: request.batch_limit,
        rows: Array.from({ length: selected }, (_, id) => ({ id: `opaque-test-row-${id}`, state: "quarantined", attempts: 8 })),
    },
    plan_sha256: HASH,
    summary: { incident_start: START, incident_end: request.incident_end, selected_count: selected, eligible_total: total, remaining: total - selected, state: "quarantined", attempts: 8 },
});
const applied = (selected = 3) => ({ applied_count: selected, plan_sha256: HASH, state: "retry", processing: "queued_for_existing_worker" });
let host, root;

beforeEach(() => {
    jest.resetAllMocks();
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-11-15T15:00:00Z"));
    global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    useAuth.mockReturnValue({ user: { id: "synthetic-owner", is_owner: true, role: "owner" }, loading: false });
    api.get.mockResolvedValue({ data: config() });
    api.post.mockImplementation(async (url, body) => ({ data: url.endsWith("/dry-run") ? review(body) : applied() }));
});

afterEach(() => {
    act(() => root.unmount());
    host.remove();
    jest.useRealTimers();
});

async function render(content = <SallaWebhookRecovery />) {
    await act(async () => root.render(<MemoryRouter>{content}</MemoryRouter>));
}
function button(text) {
    return Array.from(host.querySelectorAll("button")).find((item) => item.textContent.includes(text));
}
function changeNow(id, value) {
    const input = host.querySelector(`#${id}`);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
}
async function change(id, value) { await act(async () => changeNow(id, value)); }
async function click(text) { await act(async () => button(text).click()); }
function submitNow() { host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }
async function preview() {
    await change("recovery-incident-end", "2026-11-15T17:45:00");
    await act(async () => submitNow());
}
const applies = () => api.post.mock.calls.filter(([url]) => url.endsWith("/apply"));

test("opening the owner page reads configuration only and never polls or applies", async () => {
    await render();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe(`${API}/config`);
    await act(async () => jest.advanceTimersByTime(60_000));
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
    expect(button("تطبيق الخطة")).toBeUndefined();
});

test.each([
    { id: "employee", role: "employee", is_owner: false },
    { id: "admin", role: "admin", is_owner: false },
    null,
])("non-owner cannot load maintenance configuration or see its settings link (%j)", async (user) => {
    useAuth.mockReturnValue({ user, loading: false });
    await render(<><SallaWebhookRecovery /><SallaWebhookRecoveryLink /></>);
    expect(host.textContent).toContain("هذه الصيانة متاحة لمالك المتجر فقط");
    expect(host.querySelector('a[href="/salla-webhook-recovery"]')).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
});

test("the existing owner role can open the linked page without a generic admin bypass", async () => {
    useAuth.mockReturnValue({ user: { id: "owner", role: "owner" }, loading: false });
    await render(<><SallaWebhookRecoveryLink /><SallaWebhookRecovery /></>);
    expect(host.querySelector('a[href="/salla-webhook-recovery"]')).not.toBeNull();
    expect(api.get).toHaveBeenCalledTimes(1);
});

test.each([
    ["disabled", { enabled: false }, "غير مفعّلة"],
    ["unverified", { runtime_validated: false, runtime: null }, "تعذر التحقق من جاهزية"],
])("%s configuration cannot request a plan", async (_, overrides, message) => {
    api.get.mockResolvedValue({ data: { ...config(), ...overrides } });
    await render();
    expect(host.textContent).toContain(message);
    expect(button("معاينة خطة الاستعادة").disabled).toBe(true);
    await act(async () => submitNow());
    expect(api.post).not.toHaveBeenCalled();
    expect(button("تطبيق الخطة")).toBeUndefined();
});

test("dry-run converts explicit Riyadh time to UTC and displays only review metadata", async () => {
    const plan = review();
    plan.plan.extra_private_field = "synthetic-private-content-not-for-display";
    api.post.mockResolvedValue({ data: plan });
    await render();
    await preview();
    expect(api.post.mock.calls[0].slice(0, 2)).toEqual([`${API}/dry-run`, { incident_end: END, batch_limit: 50 }]);
    expect(applies()).toHaveLength(0);
    expect(host.querySelector('[aria-label="خطة الاستعادة المعروضة"]')).not.toBeNull();
    expect(host.textContent).toContain("المحاولات السابقة لكل حدث: 8");
    expect(host.textContent).toContain("المتبقي لدفعات لاحقة");
    expect(host.textContent).not.toContain("opaque-test-row");
    expect(host.textContent).not.toContain("synthetic-private-content");
    expect(host.textContent).not.toContain(HASH);
});

test("explicit Apply sends the exact reviewed plan once, disables auth replay, and reports queued work", async () => {
    const data = review();
    Object.freeze(data.plan.runtime); Object.freeze(data.plan);
    let finish;
    api.post.mockImplementation((url) => url.endsWith("/dry-run")
        ? Promise.resolve({ data }) : new Promise((resolve) => { finish = resolve; }));
    await render(); await preview();
    const applyButton = button("تطبيق الخطة");
    await act(async () => { applyButton.click(); applyButton.click(); });
    expect(applies()).toHaveLength(1);
    expect(applies()[0][1]).toEqual({ plan: data.plan, plan_sha256: data.plan_sha256 });
    expect(applies()[0][1].plan).toBe(data.plan);
    expect(applies()[0][2]).toMatchObject({ _mezanAuthRetried: true, timeout: 30_000 });
    await act(async () => finish({ data: applied() }));
    expect(host.textContent).toContain("أُعيدت 3 أحداث إلى قائمة المعالجة");
    expect(host.textContent).toContain("يتولى عامل النظام تحديثها");
    expect(button("تطبيق الخطة")).toBeUndefined();
    await act(async () => jest.advanceTimersByTime(60_000));
    expect(applies()).toHaveLength(1);
    expect(api.get).toHaveBeenCalledTimes(1);
});

test.each([
    ["recovery-incident-end", "2026-11-15T17:46:00"],
    ["recovery-batch-limit", "25"],
])("changing %s invalidates the review before another same-tick Apply click", async (id, value) => {
    await render(); await preview();
    const oldApply = button("تطبيق الخطة");
    await act(async () => { changeNow(id, value); oldApply.click(); });
    expect(applies()).toHaveLength(0);
    expect(button("تطبيق الخطة")).toBeUndefined();
});

test("an old dry-run response cannot restore a plan after its inputs change", async () => {
    let finish;
    api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await change("recovery-incident-end", "2026-11-15T17:45:00");
    await act(async () => { submitNow(); changeNow("recovery-batch-limit", "25"); });
    await act(async () => finish({ data: review() }));
    expect(button("تطبيق الخطة")).toBeUndefined();
    expect(host.querySelector('[aria-label="خطة الاستعادة المعروضة"]')).toBeNull();
});

test.each([
    ["2026-11-15T16:56:00", "50"],
    ["2026-11-15T18:01:00", "50"],
    ["2026-11-15T17:45:00", "51"],
    ["2026-11-15T17:45:00", "1.5"],
    ["", "50"],
])("invalid window/batch %s %s never reaches dry-run", async (end, batch) => {
    await render();
    await change("recovery-incident-end", end); await change("recovery-batch-limit", batch);
    await act(async () => submitNow());
    expect(api.post).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test("a different runtime in the returned plan cannot enable Apply", async () => {
    const data = review(); data.plan.runtime.release_id = `rg5-${"d".repeat(64)}`;
    api.post.mockResolvedValue({ data });
    await render(); await preview();
    expect(host.textContent).toContain("تعذر مطابقة المعاينة");
    expect(button("تطبيق الخطة")).toBeUndefined();
    expect(applies()).toHaveLength(0);
});

test.each([
    ["more rows than displayed", (data) => { data.plan.rows.push({ state: "quarantined", attempts: 8 }); }],
    ["missing rows", (data) => { delete data.plan.rows; }],
    ["different eligible total", (data) => { data.plan.eligible_total += 1; }],
    ["different owner", (data) => { data.plan.owner_id = "another-owner"; }],
    ["different merchant", (data) => { data.plan.merchant_id = "another-merchant"; }],
    ["different row state", (data) => { data.plan.rows[0].state = "done"; }],
    ["different attempt count", (data) => { data.plan.rows[0].attempts = 9; }],
])("a plan with %s cannot apply more or different work than the review shows", async (_, changePlan) => {
    const data = review(); changePlan(data);
    api.post.mockResolvedValue({ data });
    await render(); await preview();
    expect(host.textContent).toContain("تعذر مطابقة المعاينة");
    expect(button("تطبيق الخطة")).toBeUndefined();
    expect(applies()).toHaveLength(0);
});

test("an empty eligible plan cannot be applied", async () => {
    api.post.mockResolvedValue({ data: review(undefined, 0, 0) });
    await render(); await preview();
    expect(host.textContent).toContain("لا توجد أحداث مؤهلة للاستعادة");
    expect(button("تطبيق الخطة")).toBeUndefined();
});

test.each([
    ["worker_lease_busy", "عامل المعالجة مشغول"],
    ["plan_stale", "تغيّرت حالة الأحداث"],
    ["already_granted", "سبق منح هذه الأحداث"],
    ["runtime_changed", "تغيّرت نسخة التشغيل"],
    ["recovery_conflict", "حدث تعارض"],
    [undefined, "تعذر تأكيد نتيجة التطبيق"],
])("failed Apply %s removes the plan and never retries automatically", async (code, message) => {
    api.post.mockImplementation(async (url, request) => {
        if (url.endsWith("/dry-run")) return { data: review(request) };
        throw { response: { status: code ? 409 : 503, data: { detail: { code, message: "untrusted raw diagnostic" } } } };
    });
    await render(); await preview(); await click("تطبيق الخطة");
    expect(host.textContent).toContain(message);
    expect(host.textContent).not.toContain("untrusted raw diagnostic");
    expect(button("تطبيق الخطة")).toBeUndefined();
    await act(async () => jest.advanceTimersByTime(120_000));
    expect(applies()).toHaveLength(1);
    if (code === "runtime_changed") expect(button("معاينة خطة الاستعادة").disabled).toBe(true);
});

test("refreshing configuration invalidates an earlier plan without automatic new dry-run", async () => {
    await render(); await preview();
    await click("تحديث حالة الأداة");
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(button("تطبيق الخطة")).toBeUndefined();
});

test("queue completion and granted counts change only after explicit status refresh", async () => {
    const counts = { pending: 0, processing: 0, retry: 2, quarantined: 3, done: 7 };
    const granted = { pending: 0, processing: 0, retry: 2, quarantined: 0, done: 1 };
    api.get.mockResolvedValue({ data: { ...config(), queue: { counts, grant_counts: granted, total_granted: 3, latest_completed_at: "2026-11-15T14:50:00Z" } } });
    await render();
    const table = () => host.querySelector('[aria-label="حالة معالجة الأحداث"]');
    expect(table().textContent).toContain("الأحداث المستعادة");
    expect(table().querySelectorAll("tbody tr")[4].textContent).toContain("مكتملة71");
    api.get.mockResolvedValue({ data: { ...config(), queue: { counts: { ...counts, retry: 0, done: 9 }, grant_counts: { ...granted, retry: 0, done: 3 }, total_granted: 3, latest_completed_at: "2026-11-15T14:55:00Z" } } });
    await act(async () => jest.advanceTimersByTime(60_000));
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(table().querySelectorAll("tbody tr")[4].textContent).toContain("مكتملة71");
    await click("تحديث حالة الأداة");
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(table().querySelectorAll("tbody tr")[4].textContent).toContain("مكتملة93");
    expect(api.post).not.toHaveBeenCalled();
});

test.each([null, {}, { counts: { done: -1 } }])("malformed queue metadata %j stays unknown and cannot enable plan or Apply", async (queue) => {
    api.get.mockResolvedValue({ data: { ...config(), queue } });
    await render();
    expect(host.textContent).toContain("حالة الأحداث غير معروفة");
    expect(button("معاينة خطة الاستعادة").disabled).toBe(true);
    await act(async () => submitNow());
    expect(api.post).not.toHaveBeenCalled();
    expect(button("تطبيق الخطة")).toBeUndefined();
});

test("an oversized dry-run batch gives a safe instruction and no applicable plan", async () => {
    api.post.mockRejectedValue({ response: { status: 422, data: { detail: { code: "batch_payload_limit" } } } });
    await render(); await preview();
    expect(host.textContent).toContain("اختر عددًا أقل من الأحداث");
    expect(button("تطبيق الخطة")).toBeUndefined();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(applies()).toHaveLength(0);
});
