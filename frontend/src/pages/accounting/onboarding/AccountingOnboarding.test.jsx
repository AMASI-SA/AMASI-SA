import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AccountingOnboarding, { FINANCIAL_SECTIONS } from "./AccountingOnboarding";

const permissions = ["accounting.opening_balances.view", "accounting.opening_balances.drafts.manage", "accounting.opening_balances.review"];
const clone = value => JSON.parse(JSON.stringify(value));
const fail = (code, status = 409) => Object.assign(new Error("private database error"), { response: { status, data: { detail: { code, message: "SECRET" } } } });
function backend(initial = {}) {
    let saved = { id: "session-1", schema_version: 1, existing: false, version: 1, status: "draft", cutover: { cutover_at: "2026-10-01T00:00:00+03:00", cutover_timezone: "Asia/Riyadh", cutover_evidence_file_id: "cutover-file" }, sections: Object.fromEntries(FINANCIAL_SECTIONS.map(id => [id, { status: "not_started", evidence_file_id: null, reason: "", data: { lines: [] } }])), ...initial };
    const replay = new Map();
    const mutate = (body, update) => {
        const text = JSON.stringify(body), previous = replay.get(body.idempotency_key);
        if (previous) { if (previous !== text) throw fail("onboarding_idempotency_conflict"); return { ...clone(saved), existing: true }; }
        if (body.version !== saved.version) throw fail("onboarding_version_conflict");
        if (saved.status === "reviewed") throw fail("onboarding_session_locked");
        saved = { ...saved, ...update, version: saved.version + 1, existing: false };
        replay.set(body.idempotency_key, text); return clone(saved);
    };
    const transport = {
        getOnboardingDefinitions: jest.fn(async () => ({ schema_version: 1, financial_base: "/api/financial-provider-apps/accounting-module/financial-accounts", sections: FINANCIAL_SECTIONS, opening_categories: { prepaid_expense: { label: "مدفوع مقدمًا" }, accrued_expense: { label: "مستحق" } } })),
        listOnboardingSessions: jest.fn(async () => ({ items: [clone(saved)] })),
        getOnboardingFinancialAccounts: jest.fn(async () => ({ items: [{ id: "bank-1", name: "البنك", account_type: "bank", currency: "SAR", status: "active" }] })),
        getOnboardingIdentities: jest.fn(async kind => ({ items: kind === "external_person" ? [{ id: "person-exact", label: "طرف موجود", kind }] : [] })),
        getOnboardingSession: jest.fn(async () => clone(saved)),
        createOnboardingSession: jest.fn(async body => { saved.cutover.cutover_at = body.cutover_at; return clone(saved); }),
        saveOnboardingSection: jest.fn(async (id, section, body) => {
            if (body.status === "not_applicable" && (body.data.lines.length || !body.reason || !body.evidence_file_id)) throw fail("onboarding_not_applicable_conflict");
            return mutate(body, { status: "draft", preview: null, sections: { ...saved.sections, [section]: { status: body.status, reason: body.reason, evidence_file_id: body.evidence_file_id, data: clone(body.data) } } });
        }),
        saveOnboardingCutover: jest.fn(async (id, body) => mutate(body, { cutover: body })),
        uploadOnboardingEvidence: jest.fn(async () => ({ source_file_id: "evidence-1", sha256: "a".repeat(64), size: 4 })),
        previewOnboardingSession: jest.fn(async (id, body) => mutate(body, { status: "previewed", preview: { hash: "hash", debit_total: "0.00", credit_total: "0.00", balanced: true, zero_accounts: ["bank-1"], inventory_reconciliation: { verified: true } } })),
        reviewOnboardingSession: jest.fn(async (id, body) => mutate(body, { status: "reviewed" })),
        getOnboardingReadiness: jest.fn(async () => ({ source_ready: true, inventory_reconciled: false, blockers: [], ready_for_live_post: false })),
        createOnboardingExternalPerson: jest.fn(async value => ({ ...value, id: "new-exact-id", kind: "general" })),
    };
    return { transport, peek: () => clone(saved), stale: () => { saved.version += 1; } };
}
let root, node, id;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; id = 0; Object.defineProperty(global, "crypto", { configurable: true, value: { randomUUID: () => `request-key-${++id}` } }); node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); });
afterEach(() => { act(() => root.unmount()); node.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
async function render(transport, extras = {}) { await act(async () => root.render(<AccountingOnboarding transport={transport} accountingPermissions={permissions} {...extras} />)); }
const field = label => node.querySelector(`[aria-label="${label}"]`);
async function value(label, next) { await act(async () => { const element = field(label); const proto = element.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, "value").set.call(element, next); element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); }
async function click(text) { await act(async () => [...node.querySelectorAll("button")].find(b => b.textContent === text).click()); }
async function stage(index) { await act(async () => node.querySelectorAll("nav button")[index].click()); }
async function resume() { await value("الجلسات المحفوظة", "session-1"); await click("استعادة المحفوظ وتجاهل التعديلات المحلية"); }
async function upload() { await act(async () => { Object.defineProperty(field("رفع دليل القسم المالي"), "files", { configurable: true, value: [new File(["test"], "evidence.txt")] }); field("رفع دليل القسم المالي").dispatchEvent(new Event("change", { bubbles: true })); }); }

test("renders 16 RTL stages and server progress; edits/navigation do no autosave or accounting writes", async () => {
    const { transport } = backend(); await render(transport); await resume();
    expect(node.querySelector("main").dir).toBe("rtl"); expect(node.querySelectorAll("nav button")).toHaveLength(16);
    await stage(1); await click("اختيار جهة موجودة"); await value("الجهة 1", "bank-1"); await value("الرصيد الافتتاحي 1", "0"); await stage(2);
    expect(transport.saveOnboardingSection).not.toHaveBeenCalled(); expect(transport.previewOnboardingSession).not.toHaveBeenCalled();
    expect(node.textContent).toContain("P02 — LOCKED");
    expect([...node.querySelectorAll("button")].some(b => /^(ترحيل|تفعيل|Post|Activate)/i.test(b.textContent))).toBe(false);
});

test("explicit zero saves via 7-section contract with evidence and survives unmount/resume", async () => {
    const b = backend(); await render(b.transport); await resume(); await stage(1); await click("اختيار جهة موجودة"); await value("الجهة 1", "bank-1"); await value("الرصيد الافتتاحي 1", "0"); await upload(); await value("حالة القسم المالي", "complete"); await click("حفظ البيانات المالية");
    const [session, section, body] = b.transport.saveOnboardingSection.mock.calls[0];
    expect([session, section]).toEqual(["session-1", "banks_cash"]); expect(body.version).toBe(1);
    expect(body.data.lines[0]).toMatchObject({ financial_account_id: "bank-1", meaning: "zero", original_amount: "0", evidence_file_id: "evidence-1" });
    await act(async () => root.unmount()); root = createRoot(node); await render(b.transport); await resume(); await stage(1);
    expect(field("الرصيد الافتتاحي 1").value).toBe("0"); expect(node.textContent).toContain("الإصدار 2");
});

test("stale version retains visible edits and demands explicit reload without blind retry", async () => {
    const b = backend(); await render(b.transport); await resume(); await stage(1); await click("اختيار جهة موجودة"); await value("الجهة 1", "bank-1"); await value("الرصيد الافتتاحي 1", "55"); b.stale(); await click("حفظ البيانات المالية");
    expect(node.textContent).toContain("يلزم استعادة الجلسة"); expect(field("الرصيد الافتتاحي 1").value).toBe("55");
    expect(b.transport.saveOnboardingSection).toHaveBeenCalledTimes(1); expect(b.peek().sections.banks_cash.data.lines).toEqual([]);
    expect(node.textContent).not.toContain("SECRET");
});

test("not applicable requires reason and uploaded evidence and remains distinct from zero", async () => {
    const b = backend(); await render(b.transport); await resume(); await stage(4); await value("حالة القسم المالي", "not_applicable"); await click("حفظ البيانات المالية");
    expect(b.transport.saveOnboardingSection).not.toHaveBeenCalled();
    await value("سبب القسم المالي", "لا توجد جهات"); await upload(); await click("حفظ البيانات المالية");
    expect(b.peek().sections.suppliers).toMatchObject({ status: "not_applicable", reason: "لا توجد جهات", data: { lines: [] } });
});

test("external person uses exact selected owner identity and never name matching", async () => {
    const b = backend(); await render(b.transport); await resume(); await stage(5); await click("اختيار جهة موجودة"); await value("الجهة 1", "person-exact"); await value("مستحق لنا على الطرف 1", "14"); await click("حفظ البيانات المالية");
    expect(b.peek().sections.suppliers.data.lines[0]).toMatchObject({ category: "customer_receivable", entity_id: "person-exact", original_amount: "14" });
    expect(b.transport.createOnboardingExternalPerson).not.toHaveBeenCalled();
});

test("inventory saves per-account valuation without physical stock fields", async () => {
    const b = backend(); await render(b.transport); await resume(); await stage(9);
    await click("إضافة منتج أو مكوّن"); await click("إضافة قيمة حساب مخزون"); await value("مرجع حساب المخزون 1", "inventory-account-a"); await value("قيمة حساب المخزون 1", "12.50");
    await click("إضافة قيمة حساب مخزون"); await value("مرجع حساب المخزون 2", "inventory-account-b"); await value("قيمة حساب المخزون 2", "7.50"); await upload(); await click("حفظ البيانات المالية");
    const data = b.peek().sections.inventory.data;
    expect(data.inventory_valuation).toMatchObject({ total_sar: "20.00", account_totals: { "inventory-account-a": "12.50", "inventory-account-b": "7.50" }, manifest_hash: "a".repeat(64) });
    expect(JSON.stringify(data)).not.toMatch(/allocations|opening_quantity|product_id|resource_id|location_id/);
});

test("preview errors are safe; review locks edits and readiness never enables live controls", async () => {
    const b = backend(); b.transport.previewOnboardingSession.mockRejectedValueOnce(fail("onboarding_inventory_value_mismatch"));
    await render(b.transport); await resume(); await value("ملاحظة المعاينة والمراجعة", "مراجعة موثقة"); await click("معاينة الجلسة على الخادم");
    expect(node.textContent).toContain("قيمة المخزون لا تطابق"); expect(node.textContent).not.toContain("SECRET");
    await click("استعادة المحفوظ وتجاهل التعديلات المحلية"); await click("معاينة الجلسة على الخادم"); expect(node.querySelector('[data-testid="server-preview"]')).not.toBeNull();
    await click("مراجعة الجلسة وقفلها"); expect(node.textContent).toContain("مراجعة ومقفلة");
    expect([...node.querySelectorAll("button")].find(b => b.textContent === "حفظ البيانات المالية").closest("fieldset").disabled).toBe(true);
    await click("فحص جاهزية المصدر"); expect(node.querySelector('[data-testid="server-readiness"]')).not.toBeNull();
    expect(node.textContent).toContain("P02 — LOCKED");
});

test("view permission required; role or missing manage permission never grants writes", async () => {
    const b = backend(); await render(b.transport, { accountingPermissions: [] }); expect(b.transport.listOnboardingSessions).not.toHaveBeenCalled();
    await render(b.transport, { accountingPermissions: [permissions[0]] }); await resume();
    expect([...node.querySelectorAll("button")].find(b => b.textContent === "حفظ البيانات المالية").closest("fieldset").disabled).toBe(true);
});

test("create requires explicit cutover and never supplies owner identity", async () => {
    const b = backend(); await render(b.transport); await value("لحظة القطع للجلسة الجديدة", "2026-10-01T00:00"); await click("إنشاء جلسة");
    expect(b.transport.createOnboardingSession.mock.calls[0][0]).toEqual({ cutover_at: "2026-10-01T00:00:00+03:00", cutover_timezone: "Asia/Riyadh", idempotency_key: expect.any(String) });
});

test("lost response replay preserves other unsaved stages and sends the exact request again", async () => {
    const b = backend(); const save = b.transport.saveOnboardingSection.getMockImplementation();
    b.transport.saveOnboardingSection.mockImplementationOnce(async (...args) => { await save(...args); throw new Error("network lost"); }).mockImplementation(save);
    await render(b.transport); await resume(); await stage(5); await click("اختيار جهة موجودة"); await value("الجهة 1", "person-exact"); await value("مستحق لنا على الطرف 1", "90");
    await stage(1); await click("اختيار جهة موجودة"); await value("الجهة 1", "bank-1"); await value("الرصيد الافتتاحي 1", "4"); await click("حفظ البيانات المالية"); await click("إعادة إرسال الطلب نفسه");
    expect(b.transport.saveOnboardingSection.mock.calls[0]).toEqual(b.transport.saveOnboardingSection.mock.calls[1]);
    expect(b.peek().version).toBe(2);
    await stage(5); expect(field("مستحق لنا على الطرف 1").value).toBe("90"); expect(node.textContent).toContain("تعديلات مالية غير محفوظة");
});

test("person creation locks navigation/session switching until exact identity is selected", async () => {
    const b = backend(); let resolve;
    b.transport.createOnboardingExternalPerson.mockImplementation(() => new Promise(done => { resolve = done; }));
    await render(b.transport); await resume(); await stage(5); await click("إضافة طرف جديد"); await value("اسم الطرف", "جديد"); await value("هاتف الطرف", "0500000000"); await click("حفظ الطرف واختياره");
    expect(node.querySelectorAll("nav button")[1].disabled).toBe(true);
    expect([...node.querySelectorAll("button")].find(b => b.textContent === "استعادة المحفوظ وتجاهل التعديلات المحلية").disabled).toBe(true);
    await act(async () => resolve({ id: "new-exact-id", name: "جديد", kind: "general" }));
    expect(field("الجهة 1").value).toBe("new-exact-id"); expect(node.querySelectorAll("nav button")[1].disabled).toBe(false);
});

test("reviewed session is immutable but every screen stays navigable", async () => {
    const b = backend({ status: "reviewed" }); await render(b.transport); await resume(); await stage(9);
    expect(node.querySelector("h1")).not.toBeNull(); expect(node.querySelectorAll("nav button")[9].disabled).toBe(false);
    expect([...node.querySelectorAll("button")].find(b => b.textContent === "إضافة قيمة حساب مخزون").closest("fieldset").disabled).toBe(true);
    expect(b.transport.saveOnboardingSection).not.toHaveBeenCalled();
});
