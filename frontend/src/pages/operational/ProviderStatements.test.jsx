import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProviderStatements from "./ProviderStatements";
import { operationalApi as api, requestId } from "./api";
import { useOptionalAuth } from "../../context/AuthContext";
import { statementMoney, statementActionHref } from "./providerStatementPresentation";

jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("./api", () => ({ operationalApi: { providerStatements: jest.fn(), providerStatementReview: jest.fn(),
    uploadProviderStatement: jest.fn(), confirmProviderStatement: jest.fn(),
    providerStatementSavedFiles: jest.fn(), useSavedProviderStatement: jest.fn() }, requestId: jest.fn() }));

const scope = "owner-one-period-one", stamp = "2026-11-15T08:00:00Z";
const snapshot = { session_scope: scope, active: true, revision: 7, started_at: "2026-11-01T00:00:00Z", as_of: stamp,
    permissions: { upload: true, confirm: true } };
const counts = { matched: 1, missing: 1, different: 1, blocked: 1, confirmed: 0 };
const statement = { id: "statement-one", provider: "tabby", statement_reference: "SYNTHETIC-STATEMENT", filename: "tabby.xlsx",
    file_hash: "f".repeat(64), native_currency: "SAR", row_count: 4, uploaded_at: stamp };
function fixture(provider = "tabby") {
    const row = { provider, kind: "capture", native_currency: "SAR", principal_amount: "100.00", fee_net: "7.91", fee_vat: "1.19",
        fee_total: "9.10", event_date: stamp, status: "matched", reason_code: null, message: "طابقت العملية السجل المحفوظ.",
        existing: [], can_confirm: false, allowed_actions: [] };
    return { ...snapshot, statement: { ...statement, provider }, preview_hash: "p".repeat(64), counts: { ...counts }, bank_posting_allowed: false, rows: [
        { ...row, id: "matched", order_number: "1001", existing: [{ id: "saved-sale", kind: "capture", amount: "100.00", reference: "STAFF-SALE", financial_effect: "already_recorded" }] },
        { ...row, id: "missing", order_number: "1002", status: "missing", can_confirm: true, allowed_actions: ["accept_statement"], message: "رسوم قابلة للمطابقة." },
        { ...row, id: "different", order_number: "1003", kind: "refund", principal_amount: "-50.00", fee_net: "-3.96", fee_vat: "-0.59", fee_total: "-4.55",
            status: "different", can_confirm: true, allowed_actions: ["accept_statement"], message: "تحتاج الرسوم إلى اعتماد المطابقة.",
            existing: [{ id: "saved-refund", kind: "refund", amount: "-50.00", reference: "STAFF-REFUND", financial_effect: "already_recorded" }] },
        { ...row, id: "blocked", order_number: "1004", status: "blocked", principal_amount: null, fee_net: null, fee_vat: null, fee_total: null,
            reason_code: "statement_amount_missing", message: "statement_amount_missing", action_url: "/operational-balances/customer-returns", action_label: "مراجعة رد العميل" },
    ] };
}
const history = (patch = {}) => ({ ...snapshot, items: [{ ...statement, counts: { ...counts }, status: "needs_review" }], ...patch });
const savedFile = { id: "legacy-tabby-one", provider: "tabby", filename: "saved-tabby.xlsx", statement_reference: "SAVED-TABBY",
    uploaded_at: stamp, file_hash: "a".repeat(64), original_available: true, can_import: true, unavailable_reason: null };
const savedFiles = (patch = {}) => ({ session_scope: scope, active: true, started_at: snapshot.started_at, as_of: stamp,
    permissions: { upload: true }, items: [{ ...savedFile }], has_more: false, ...patch });
const confirmation = (ids = ["missing"], patch = {}) => {
    const result = fixture();
    result.rows = result.rows.map(row => ids.includes(row.id) ? { ...row, status: "already_confirmed", can_confirm: false, allowed_actions: [] } : row);
    return { ...result, revision: 8, preview_hash: "q".repeat(64), applied_row_ids: ids, replayed: false, ...patch };
};
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
let root, host, mounted;
const render = async (props = {}) => { await act(async () => root.render(<ProviderStatements {...props} />)); mounted = true; };
const button = text => [...host.querySelectorAll("button")].find(node => node.textContent.trim() === text);
const click = async text => { await act(async () => button(text).click()); };
const review = async () => click("عرض المطابقة");
const submit = async selector => act(async () => host.querySelector(selector).dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
const choose = async (file = new File(["synthetic statement"], "statement.xlsx")) => {
    const input = host.querySelector('input[type="file"]');
    await act(async () => { Object.defineProperty(input, "files", { configurable: true, value: [file] }); input.dispatchEvent(new Event("change", { bubbles: true })); });
    return file;
};
const check = async label => act(async () => host.querySelector(`[aria-label="${label}"]`).click());
const select = async (label, value) => act(async () => {
    const input = host.querySelector(`[aria-label="${label}"]`);
    input.value = value; input.dispatchEvent(new Event("change", { bubbles: true }));
});
const visibility = async value => act(async () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value }); document.dispatchEvent(new Event("visibilitychange"));
});
const online = async value => act(async () => {
    Object.defineProperty(navigator, "onLine", { configurable: true, value }); window.dispatchEvent(new Event(value ? "online" : "offline"));
});
async function chooseMissing() { await review(); await check("اختيار العملية 1002"); await check("تأكيد مراجعة العمليات المختارة"); }

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.resetAllMocks(); Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    useOptionalAuth.mockReturnValue({ user: { id: "owner-one", role: "owner" }, loading: false, authStatus: "authenticated" });
    api.providerStatements.mockResolvedValue(history()); api.providerStatementReview.mockResolvedValue(fixture());
    api.uploadProviderStatement.mockResolvedValue(fixture()); api.confirmProviderStatement.mockResolvedValue(confirmation());
    api.providerStatementSavedFiles.mockResolvedValue(savedFiles()); api.useSavedProviderStatement.mockResolvedValue(fixture());
    let serial = 0; requestId.mockImplementation(() => `intent-${++serial}`);
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); mounted = false;
});
afterEach(async () => { if (mounted) await act(async () => root.unmount()); host.remove(); delete document.visibilityState; delete navigator.onLine; });

test.each([null, { id: "employee", role: "employee" }])("non-owner %j cannot load statements or upload", async user => {
    useOptionalAuth.mockReturnValue({ user, loading: false }); await render();
    expect(host.textContent).toContain("لمالك الحساب فقط"); expect(host.querySelector("form")).toBeNull();
    expect(api.providerStatements).not.toHaveBeenCalled(); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test("auth checks hide the entire private workspace and make no reads", async () => {
    useOptionalAuth.mockReturnValue({ user: { id: "owner-one", role: "owner" }, loading: true }); await render();
    expect(host.textContent).toContain("التحقق من الجلسة"); expect(api.providerStatements).not.toHaveBeenCalled();
});

test("owner opens saved history with one cancellable read and no automatic financial actions", async () => {
    await render();
    expect(host.textContent).toContain("SYNTHETIC-STATEMENT");
    expect(api.providerStatements).toHaveBeenCalledTimes(1);
    expect(api.providerStatements).toHaveBeenCalledWith({ signal: expect.any(AbortSignal), _mezanAuthDeadlineAt: expect.any(Number) });
    expect(api.uploadProviderStatement).not.toHaveBeenCalled(); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    expect(host.querySelector('a[href="/operational-balances/financial-position?view=providers"]')).not.toBeNull();
});

test("hidden mount defers its GET; returning or focusing never repeats a completed read", async () => {
    await visibility("hidden"); await render(); expect(api.providerStatements).not.toHaveBeenCalled();
    await visibility("visible"); expect(api.providerStatements).toHaveBeenCalledTimes(1);
    await visibility("hidden"); await visibility("visible"); await online(false); await online(true);
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(api.providerStatements).toHaveBeenCalledTimes(1); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test("aborted review ignores the late response and requires an explicit fresh GET", async () => {
    const late = deferred(); api.providerStatementReview.mockReturnValueOnce(late.promise);
    await render(); await review(); const signal = api.providerStatementReview.mock.calls[0][1].signal;
    await visibility("hidden"); expect(signal.aborted).toBe(true); await visibility("visible");
    await act(async () => late.resolve(fixture()));
    expect(api.providerStatementReview).toHaveBeenCalledTimes(1); expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull();
    await review(); expect(api.providerStatementReview).toHaveBeenCalledTimes(2);
});

test("new authenticated owner cannot see a previous owner's late history", async () => {
    const late = deferred(); api.providerStatements.mockReturnValueOnce(late.promise);
    await render(); const signal = api.providerStatements.mock.calls[0][0].signal;
    useOptionalAuth.mockReturnValue({ user: { id: "owner-two", role: "owner" }, loading: false, authStatus: "authenticated" });
    api.providerStatements.mockResolvedValue(history({ session_scope: "owner-two-scope", items: [] })); await render();
    expect(signal.aborted).toBe(true);
    await act(async () => late.resolve(history()));
    expect(host.textContent).not.toContain("SYNTHETIC-STATEMENT"); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test.each(["tabby", "tamara", "salla"])("explicit upload displays server-detected %s without provider inference or follow-up requests", async provider => {
    api.uploadProviderStatement.mockResolvedValue(fixture(provider)); await render();
    const file = await choose(new File(["statement"], "any-filename.xlsx"));
    expect(api.uploadProviderStatement).not.toHaveBeenCalled();
    await click("رفع الكشف ومطابقته");
    expect(api.uploadProviderStatement).toHaveBeenCalledWith(file, { request_id: "intent-1", expected_session_scope: scope, expected_started_at: snapshot.started_at }, { signal: expect.any(AbortSignal) });
    expect(host.querySelector('[data-testid="provider-statement-review"]')).not.toBeNull();
    expect(host.textContent).toContain({ tabby: "تابي · مطابقة", tamara: "تمارا · مطابقة", salla: "سلة · مطابقة" }[provider]);
    expect(api.providerStatements).toHaveBeenCalledTimes(1); expect(api.providerStatementReview).not.toHaveBeenCalled(); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

test.each(["statement.csv", "statement.xls", "statement.pdf"])("unsupported %s never uploads", async filename => {
    await render(); await choose(new File(["file"], filename)); await submit('[data-testid="provider-statement-upload"]');
    expect(host.textContent).toContain(".xlsx"); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test("oversized and empty files stay local", async () => {
    await render(); const large = new File(["file"], "large.xlsx"); Object.defineProperty(large, "size", { value: 10 * 1024 * 1024 + 1 });
    await choose(large); expect(host.textContent).toContain("10 ميجابايت"); expect(button("رفع الكشف ومطابقته").disabled).toBe(true);
    await choose(new File([], "empty.xlsx")); await submit('[data-testid="provider-statement-upload"]'); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test("comparison preserves large decimal strings, unknown versus zero, and refund signs", async () => {
    const result = fixture(); result.rows[0].principal_amount = "9007199254740993.25"; result.rows[0].fee_vat = "0.00";
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    const matched = host.querySelector('[data-testid="statement-row-matched"]'), blocked = host.querySelector('[data-testid="statement-row-blocked"]');
    expect(matched.textContent).toContain("9,007,199,254,740,993.25"); expect(matched.textContent).toContain("0.00");
    expect(blocked.textContent).toContain("غير متاح"); expect(blocked.textContent).not.toContain("0.00");
    expect(host.querySelector('[data-testid="statement-row-different"]').textContent).toContain("-4.55");
    expect(blocked.textContent).not.toContain("statement_amount_missing"); expect(blocked.querySelector('a[href="/operational-balances/customer-returns"]')).not.toBeNull();
});

test("every existing record requires an explicit choice before confirmation, including a sole candidate", async () => {
    api.confirmProviderStatement.mockResolvedValue(confirmation(["different"])); await render(); await review();
    expect(host.querySelector('[data-testid="statement-row-blocked"] input')).toBeNull();
    expect(host.querySelector('[data-testid="statement-row-matched"] input')).toBeNull();
    await check("اختيار العملية 1003"); await check("تأكيد مراجعة العمليات المختارة");
    expect(button("اعتماد العمليات المختارة").disabled).toBe(true);
    expect(host.querySelector('[aria-label="السجل المقابل للعملية 1003"]').value).toBe("");
    await select("السجل المقابل للعملية 1003", "saved-refund");
    expect(host.querySelector('[aria-label="تأكيد مراجعة العمليات المختارة"]').checked).toBe(false);
    await check("تأكيد مراجعة العمليات المختارة"); await click("اعتماد العمليات المختارة");
    expect(api.confirmProviderStatement).toHaveBeenCalledWith("statement-one", { request_id: "intent-1", expected_session_scope: scope,
        expected_preview_hash: "p".repeat(64), decisions: [{ row_id: "different", action: "accept_statement", existing_id: "saved-refund" }], confirmed: true }, { signal: expect.any(AbortSignal) });
    expect(api.providerStatementReview).toHaveBeenCalledTimes(1); expect(api.providerStatements).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="statement-row-different"] input')).toBeNull();
});

test("a missing safe candidate can only be confirmed after both explicit selections", async () => {
    await render(); await review(); await submit('[data-testid="provider-statement-review"] form'); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    await check("اختيار العملية 1002"); expect(button("اعتماد العمليات المختارة").disabled).toBe(true);
    await check("تأكيد مراجعة العمليات المختارة"); await click("اعتماد العمليات المختارة");
    expect(api.confirmProviderStatement.mock.calls[0][1].decisions).toEqual([{ row_id: "missing", action: "accept_statement" }]);
});

test("duplicate submit clicks cannot dispatch two financial commands", async () => {
    const late = deferred(); api.confirmProviderStatement.mockReturnValue(late.promise);
    await render(); await chooseMissing();
    await act(async () => { const form = host.querySelector('[data-testid="provider-statement-review"] form');
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(api.confirmProviderStatement).toHaveBeenCalledTimes(1);
    await act(async () => late.resolve(confirmation()));
});

test("uncertain upload keeps the exact file and request identity for an explicit retry", async () => {
    api.uploadProviderStatement.mockRejectedValueOnce({ code: "ERR_NETWORK" }).mockResolvedValueOnce({ ...fixture(), duplicate: true });
    await render(); const file = await choose(); await click("رفع الكشف ومطابقته");
    expect(host.querySelector('input[type="file"]').disabled).toBe(true);
    await visibility("hidden"); await visibility("visible"); expect(api.uploadProviderStatement).toHaveBeenCalledTimes(1);
    await click("إعادة نفس محاولة الرفع");
    expect(api.uploadProviderStatement.mock.calls[1][0]).toBe(file);
    expect(api.uploadProviderStatement.mock.calls[1][1]).toEqual(api.uploadProviderStatement.mock.calls[0][1]);
    expect(requestId).toHaveBeenCalledTimes(1); expect(host.textContent).toContain("محفوظ مسبقًا");
    expect(api.providerStatements).toHaveBeenCalledTimes(1);
});

test("uncertain confirmation replays the exact reviewed decisions and request ID", async () => {
    api.confirmProviderStatement.mockRejectedValueOnce({ code: "ECONNABORTED" }).mockResolvedValueOnce(confirmation(["missing"], { replayed: true }));
    await render(); await chooseMissing(); await click("اعتماد العمليات المختارة");
    await click("إعادة نفس محاولة الاعتماد");
    expect(api.confirmProviderStatement.mock.calls[1][1]).toEqual(api.confirmProviderStatement.mock.calls[0][1]);
    expect(requestId).toHaveBeenCalledTimes(1); expect(host.textContent).toContain("هذا الاعتماد مسجل مسبقًا");
});

test.each(["hidden", "offline"])("%s consumes an upload attempt; a later response or return cannot launch more work", async departure => {
    const late = deferred(); api.uploadProviderStatement.mockReturnValueOnce(late.promise);
    await render(); await choose(); await click("رفع الكشف ومطابقته");
    const signal = api.uploadProviderStatement.mock.calls[0][2].signal;
    if (departure === "hidden") { await visibility("hidden"); await visibility("visible"); } else { await online(false); await online(true); }
    expect(signal.aborted).toBe(true);
    await act(async () => late.resolve(fixture()));
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull();
    expect(api.uploadProviderStatement).toHaveBeenCalledTimes(1); expect(api.providerStatements).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("لم نتأكد من نتيجة الرفع");
    await click("إعادة نفس محاولة الرفع");
    expect(api.uploadProviderStatement.mock.calls[1][1]).toEqual(api.uploadProviderStatement.mock.calls[0][1]);
});

test.each(["unmount", "auth", "auth_loading"])("%s aborts confirmation and fences its eventual result without follow-up reads", async departure => {
    const late = deferred(); api.confirmProviderStatement.mockReturnValueOnce(late.promise);
    await render(); await chooseMissing(); await click("اعتماد العمليات المختارة");
    const signal = api.confirmProviderStatement.mock.calls[0][2].signal;
    if (departure === "unmount") { await act(async () => root.unmount()); mounted = false; }
    else {
        useOptionalAuth.mockReturnValue(departure === "auth_loading" ? { user: { id: "owner-one", role: "owner" }, loading: true }
            : { user: { id: "employee-two", role: "employee" }, loading: false }); await render();
    }
    expect(signal.aborted).toBe(true); await act(async () => late.resolve(confirmation()));
    expect(host.textContent).not.toContain("حُفظ اعتماد العمليات"); expect(host.textContent).not.toContain("STAFF-REFUND");
    expect(api.providerStatements).toHaveBeenCalledTimes(1); expect(api.providerStatementReview).toHaveBeenCalledTimes(1);
});

test.each([
    result => ({ ...result, session_scope: "other-session" }),
    result => ({ ...result, started_at: "2026-11-14T00:00:00Z" }),
    result => ({ ...result, statement: { ...result.statement, id: "other-statement" } }),
    result => ({ ...result, bank_posting_allowed: true }),
    result => ({ ...result, rows: result.rows.map(row => ({ ...row, fee_total: 9.10 })) }),
])("unbound or malformed review response never admits confirmation", async change => {
    api.providerStatementReview.mockResolvedValue(change(fixture())); await render(); await review();
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull(); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test("confirmation without complete applied-row evidence remains uncertain", async () => {
    api.confirmProviderStatement.mockResolvedValue(confirmation([], {})); await render(); await chooseMissing(); await click("اعتماد العمليات المختارة");
    expect(host.textContent).toContain("لم نتأكد من نتيجة الاعتماد"); expect(button("إعادة نفس محاولة الاعتماد")).toBeDefined();
    expect(host.textContent).not.toContain("حُفظ اعتماد العمليات المختارة");
});

test.each([{ applied: ["missing", "different"] }, { applied: ["missing", "missing"] }])("extra or duplicate applied IDs %j cannot be reported as the selected approval", async ({ applied }) => {
    api.confirmProviderStatement.mockResolvedValue(confirmation(applied)); await render(); await chooseMissing(); await click("اعتماد العمليات المختارة");
    expect(host.textContent).toContain("لم نتأكد من نتيجة الاعتماد");
    expect(host.textContent).not.toContain("حُفظ اعتماد العمليات المختارة");
    expect(button("إعادة نفس محاولة الاعتماد")).toBeDefined();
});

test("stale confirmation requires fresh review and new user selections, never an automatic replay", async () => {
    api.confirmProviderStatement.mockRejectedValue({ response: { status: 409, data: { detail: { code: "statement_preview_stale" } } } });
    await render(); await chooseMissing(); await click("اعتماد العمليات المختارة");
    expect(host.textContent).toContain("تغيرت المطابقة");
    api.providerStatementReview.mockResolvedValue({ ...fixture(), preview_hash: "new-preview" });
    await click("قراءة المطابقة مجددًا");
    expect(api.confirmProviderStatement).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[aria-label="اختيار العملية 1002"]').checked).toBe(false);
    expect(host.querySelector('[aria-label="تأكيد مراجعة العمليات المختارة"]').checked).toBe(false);
    expect(button("اعتماد العمليات المختارة").disabled).toBe(true);
});

test.each([{ active: false }, { permissions: { upload: false, confirm: false } }])("server restrictions %j preserve reads and block financial writes", async patch => {
    api.providerStatements.mockResolvedValue(history(patch)); api.providerStatementReview.mockResolvedValue({ ...fixture(), ...patch });
    await render(); expect(host.querySelector('input[type="file"]').disabled).toBe(true); await review();
    expect(host.querySelector('[aria-label="اختيار العملية 1002"]').disabled).toBe(true);
    expect(api.uploadProviderStatement).not.toHaveBeenCalled(); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

test("arbitrary action URLs are not rendered as correction links", async () => {
    const result = fixture(); result.rows[3].action_url = "https://unrelated.example/financial-write";
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    expect(host.querySelector('[data-testid="statement-row-blocked"] a')).toBeNull();
    for (const value of ["javascript:alert(1)", "//outside.example/", "/\\outside.example/", "/payment-settlements"]) expect(statementActionHref(value)).toBeNull();
    expect(statementMoney(null)).toBe("غير متاح"); expect(statementMoney("0.00")).toBe("0.00"); expect(statementMoney("NaN")).toBe("غير متاح");
});

test("file errors are shown in Arabic without raw parser codes", async () => {
    api.uploadProviderStatement.mockRejectedValue({ response: { status: 422, data: { detail: { code: "provider_statement_parse_invalid", message: "statement_provider_ambiguous" } } } });
    await render(); await choose(); await click("رفع الكشف ومطابقته");
    expect(host.textContent).toContain("علامات أكثر من منصة"); expect(host.textContent).not.toContain("statement_provider_ambiguous");
    expect(host.querySelector('input[type="file"]').disabled).toBe(false);
});

test("a new operating session discards a file selected under the old session", async () => {
    await render(); await choose();
    api.providerStatements.mockResolvedValue(history({ started_at: "2026-11-15T00:00:00Z", revision: 0, items: [] }));
    await click("قراءة سجل الكشوف");
    expect(button("رفع الكشف ومطابقته").disabled).toBe(true);
    expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test("selecting another file removes the previous review and its confirmation choices", async () => {
    await render(); await chooseMissing(); expect(button("اعتماد العمليات المختارة").disabled).toBe(false);
    await choose(new File(["another"], "another.xlsx"));
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull();
    expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

test.each(["refund", "capture"])("a new provider %s shows its exact proposed effect and requires an additional unrecorded acknowledgement", async kind => {
    const result = fixture();
    result.rows[1] = { ...result.rows[1], kind, allowed_actions: [`record_missing_${kind}`], principal_amount: kind === "refund" ? "-25.00" : "100.00",
        proposed_effect: { kind: `provider_${kind}`, principal_amount: kind === "refund" ? "25.00" : "100.00", native_currency: "SAR", provider: "tabby",
            remaining_before: kind === "refund" ? "100.00" : "0.00", remaining_after: kind === "refund" ? "75.00" : "100.00", bank_posting_allowed: false } };
    api.providerStatementReview.mockResolvedValue(result); await render(); await chooseMissing();
    expect(host.querySelector('[data-testid="statement-row-missing"]').textContent).toContain("ذمة المنصة من أصل المدفوع");
    expect(host.textContent).toContain("الأرصدة الافتتاحية");
    expect(button("اعتماد العمليات المختارة").disabled).toBe(true);
    await submit('[data-testid="provider-statement-review"] form'); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    await check("تأكيد أن العمليات الجديدة غير مسجلة سابقًا"); await click("اعتماد العمليات المختارة");
    expect(api.confirmProviderStatement.mock.calls[0][1]).toEqual({ request_id: "intent-1", expected_session_scope: scope, expected_preview_hash: "p".repeat(64),
        decisions: [{ row_id: "missing", action: `record_missing_${kind}` }], confirmed: true, confirmed_unrecorded: true });
});

function employeeRefundFixture() {
    const result = fixture();
    result.rows[1] = { ...result.rows[1], kind: "refund", principal_amount: "25.00", allowed_actions: ["record_missing_refund"],
        existing: [{ kind: "customer_return", id: "employee-proof", amount: "25.00", reference: "STAFF-REFUND-PROOF", financial_effect: "evidence_only" }],
        proposed_effect: { kind: "provider_refund", provider: "tabby", native_currency: "SAR", principal_amount: "25.00",
            remaining_before: "100.00", remaining_after: "75.00", bank_posting_allowed: false, employee_case_id: "employee-proof" } };
    return result;
}

test("pending employee refund evidence needs an explicit in-place record choice and unrecorded acknowledgement", async () => {
    api.providerStatementReview.mockResolvedValue(employeeRefundFixture()); await render(); await review();
    expect(host.querySelector('[data-testid="provider-statement-review"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="statement-row-missing"]').textContent).toContain("سجل الموظف القائم");
    await check("اختيار العملية 1002");
    expect(host.querySelector('[aria-label="السجل المقابل للعملية 1002"]').value).toBe("");
    await check("تأكيد مراجعة العمليات المختارة"); await check("تأكيد أن العمليات الجديدة غير مسجلة سابقًا");
    await submit('[data-testid="provider-statement-review"] form'); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    await select("السجل المقابل للعملية 1002", "employee-proof");
    expect(button("اعتماد العمليات المختارة").disabled).toBe(true);
    await check("تأكيد مراجعة العمليات المختارة"); await check("تأكيد أن العمليات الجديدة غير مسجلة سابقًا");
    await click("اعتماد العمليات المختارة");
    expect(api.confirmProviderStatement.mock.calls[0][1]).toEqual({ request_id: "intent-1", expected_session_scope: scope, expected_preview_hash: "p".repeat(64),
        decisions: [{ row_id: "missing", action: "record_missing_refund", existing_id: "employee-proof" }], confirmed: true, confirmed_unrecorded: true });
});

test.each(["already_recorded", "bank_recorded", "different_case", "multiple_cases", "wrong_kind"])("employee refund %s cannot broaden an in-place confirmation", async invalid => {
    const result = employeeRefundFixture(), row = result.rows[1];
    if (["already_recorded", "bank_recorded"].includes(invalid)) row.existing[0].financial_effect = invalid;
    if (invalid === "different_case") row.proposed_effect.employee_case_id = "other-case";
    if (invalid === "multiple_cases") row.existing.push({ ...row.existing[0], id: "other-case" });
    if (invalid === "wrong_kind") row.existing[0].kind = "capture";
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull(); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

test("the original statement plan and source row remain visible without inferring missing installments", async () => {
    const result = fixture(); result.rows[0].plan_id = "Installments: 11 Months"; result.rows[0].source_row = 7; result.rows[0].payment_method = "tabby";
    result.rows[1].plan_id = "Installments: 5 Months";
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    const matched = host.querySelector('[data-testid="statement-row-matched"]');
    expect(matched.textContent).toContain("خطة الدفع الواردة بالكشف"); expect(matched.textContent).toContain("Installments: 11 Months");
    expect(matched.textContent).toContain("سطر الكشف: 7");
    expect(host.querySelector('[data-testid="statement-row-missing"]').textContent).toContain("Installments: 5 Months");
    expect(host.querySelector('[data-testid="statement-row-blocked"]').textContent).toContain("غير مذكورة في الكشف");
});

test("saved source time and newer statement evidence time have distinct labels", async () => {
    api.providerStatementReview.mockResolvedValue({ ...fixture(), statement_evidence_as_of: "2026-11-15T10:00:00Z" });
    await render(); await review();
    const header = host.querySelector('[data-testid="provider-statement-review"] header');
    expect(header.textContent).toContain("مصادر الأرصدة كما في"); expect(header.textContent).toContain("دليل رسوم الكشف كما في");
    expect(header.textContent).toContain("11:00"); expect(header.textContent).toContain("13:00");
});

test("Tamara installment count is shown as payments, with no inferred duration", async () => {
    const result = fixture("tamara"); result.rows[0].plan_id = "PAY_BY_INSTALMENTS"; result.rows[0].installments = 4;
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    const row = host.querySelector('[data-testid="statement-row-matched"]');
    expect(row.textContent).toContain("نوع الدفع الوارد بالكشف: PAY_BY_INSTALMENTS");
    expect(row.textContent).toContain("عدد الدفعات في الكشف: 4");
    expect(row.textContent).not.toContain("أشهر");
    expect(host.querySelector('[data-testid="statement-row-blocked"]').textContent).toContain("عدد الدفعات في الكشف: غير مذكور في الكشف");
});

test.each([
    { bank_posting_allowed: true }, { remaining_before: null }, { remaining_after: "NaN" },
    { provider: "tamara" }, { native_currency: "USD" }, { kind: "provider_refund" },
])("unproved missing-event effect %j never offers a financial confirmation", async patch => {
    const result = fixture(); result.rows[1] = { ...result.rows[1], allowed_actions: ["record_missing_capture"], proposed_effect: {
        kind: "provider_capture", provider: "tabby", native_currency: "SAR", principal_amount: "100.00", remaining_before: "0.00", remaining_after: "100.00", bank_posting_allowed: false, ...patch,
    } };
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull();
    expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

test("review preserves the saved upload date when its detail does not repeat history metadata", async () => {
    const result = fixture(); delete result.statement.uploaded_at;
    api.providerStatementReview.mockResolvedValue(result); await render();
    const savedDate = host.querySelector('[aria-label="الكشوف المحفوظة"] tbody tr').cells[1].textContent;
    await review();
    expect(host.querySelector('[aria-label="الكشوف المحفوظة"] tbody tr').cells[1].textContent).toBe(savedDate);
    expect(savedDate).not.toBe("غير متاح");
});

test("unprocessed history has unknown review counts and never triggers per-file reads on mount", async () => {
    api.providerStatements.mockResolvedValue(history({ items: [{ ...statement, counts: null, reviewed_at: null, review_as_of: null, status: "needs_review" }], has_more: true }));
    await render(); const saved = host.querySelector('[aria-label="الكشوف المحفوظة"]');
    expect(saved.textContent).toContain("لم تُحفظ مطابقة بعد"); expect(saved.textContent).not.toContain("مطابق: 0");
    expect(saved.textContent).toContain("توجد كشوف أقدم محفوظة");
    expect(api.providerStatementReview).not.toHaveBeenCalled(); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
    await review(); expect(api.providerStatementReview).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[aria-label="الكشوف المحفوظة"]').textContent).toContain("لم تُحفظ مطابقة بعد");
});

test("fresh review does not mislabel new comparison counts with the old saved review timestamp", async () => {
    api.providerStatements.mockResolvedValue(history({ items: [{ ...statement, counts: { matched: 0, missing: 4, different: 0, blocked: 0, confirmed: 0 }, reviewed_at: stamp, review_as_of: stamp }] }));
    await render(); await review();
    const cached = host.querySelector('[aria-label="الكشوف المحفوظة"]');
    expect(cached.textContent).toContain("غير مسجل: 4"); expect(cached.textContent).toContain("آخر نتيجة مطابقة");
    expect(host.querySelector('[data-testid="provider-statement-review"] .op-summary').textContent).toContain("مطابق1");
});

const average = { available: true, provider: "tabby", native_currency: "SAR", calculation: "sale_variable_fees_before_vat_over_sale_principal",
    rate_percent: "7.47000000", principal_total: "200.00", fee_variable_net_total: "14.94", fee_fixed_net_total: "2.50", fixed_amount_per_sale: "1.25000000",
    vat_rate: "0.15", vat_rate_percent: "15.00000000", vat_rate_status: "explicit_uniform", full_estimate_requires_vat: false,
    fee_net_total: "17.44", fee_vat_total: "2.62", fee_total: "20.06", eligible_sale_rows: 2,
    statement_id: "prior-valid-statement", statement_reference: "SYNTHETIC-AVERAGE-02", source_as_of: stamp, source_date_basis: "sale_event_date", accepted_at: stamp };

test("source-derived average is labeled estimated with its own sales totals and source; actual row fees stay exact", async () => {
    api.providerStatementReview.mockResolvedValue({ ...fixture(), statement_average: average });
    api.providerStatements.mockResolvedValue(history({ items: [{ ...statement, counts, statement_average: average }] }));
    await render(); await review();
    const summary = host.querySelector('[aria-label="متوسط رسوم المنصة"]');
    expect(summary.textContent).toContain("7.47%"); expect(summary.textContent).toContain("تقديري");
    expect(summary.textContent).toContain("متوسط العمولة المتغيرة قبل الضريبة");
    expect(summary.textContent).toContain("200.00 SAR"); expect(summary.textContent).toContain("14.94 SAR");
    expect(summary.textContent).toContain("رسم ثابت تقديري لكل عملية1.25 SAR"); expect(summary.textContent).toContain("نسبة الضريبة المثبتة15%");
    expect(summary.textContent).toContain("عمولة التحويلتُراجع مستقلة");
    expect(summary.textContent).toContain("عمليات البيع الداخلة في المتوسط2"); expect(summary.textContent).toContain("SYNTHETIC-AVERAGE-02");
    expect(summary.textContent).toContain("تاريخ البيع في الكشف");
    expect(host.querySelector('[data-testid="statement-row-matched"]').textContent).toContain("9.10");
    expect(host.querySelector('[aria-label="الكشوف المحفوظة"]').textContent).toContain("متوسط تقديري: 7.47%");
});

test.each([{ available: false }, { rate_percent: null }, { rate_percent: 9.1 }, { provider: "tamara" }, { native_currency: "USD" }, { eligible_sale_rows: 0 }, { calculation: "inclusive_average" }])(
    "unknown or unbound average %j never becomes a default percentage", async patch => {
        api.providerStatementReview.mockResolvedValue({ ...fixture(), statement_average: { ...average, ...patch } });
        await render(); await review(); const summary = host.querySelector('[aria-label="متوسط رسوم المنصة"]');
        expect(summary.textContent).toContain("لا يتوفر متوسط معتمد"); expect(summary.textContent).not.toContain("9.1%"); expect(summary.textContent).not.toContain("0%");
    });

test("missing uniform VAT preserves the variable average but never invents a complete cost estimate", async () => {
    api.providerStatementReview.mockResolvedValue({ ...fixture(), statement_average: { ...average, vat_rate: null, vat_rate_percent: null,
        vat_rate_status: "missing", full_estimate_requires_vat: true, fee_vat_total: null } });
    await render(); await review(); const summary = host.querySelector('[aria-label="متوسط رسوم المنصة"]');
    expect(summary.textContent).toContain("7.47%"); expect(summary.textContent).toContain("نسبة الضريبة غير متاحة بصورة موحدة");
    expect(summary.textContent).toContain("ضريبة رسوم مبيعات المصدرغير متاح SAR"); expect(summary.textContent).not.toContain("15%");
});

test.each([
    ["statement_average_no_sales", "لا يحتوي هذا الكشف على مبيعات صالحة"],
    ["statement_average_component_split_missing", "لا يفصل الكشف العمولة المتغيرة عن الرسم الثابت"],
    ["statement_average_sale_evidence_incomplete", "بيانات المبيعات أو العملة أو مكونات الرسوم غير مكتملة"],
])("unavailable average %s explains its source limitation without hiding actual fees", async (reason_code, label) => {
    api.providerStatementReview.mockResolvedValue({ ...fixture(), statement_average: { available: false, reason_code } });
    await render(); await review(); const summary = host.querySelector('[aria-label="متوسط رسوم المنصة"]');
    expect(summary.textContent).toContain(label); expect(summary.textContent).toContain("الرسوم الفعلية المكتملة");
    expect(summary.textContent).not.toContain("0%"); expect(summary.textContent).not.toContain(reason_code);
    expect(host.querySelector('[data-testid="statement-row-matched"]').textContent).toContain("9.10");
    await check("اختيار العملية 1002"); await check("تأكيد مراجعة العمليات المختارة");
    expect(button("اعتماد العمليات المختارة").disabled).toBe(false);
});

test("conflicting source VAT keeps the variable average and explicitly holds the full estimate", async () => {
    api.providerStatementReview.mockResolvedValue({ ...fixture(), statement_average: { ...average, vat_rate: null, vat_rate_percent: null,
        vat_rate_status: "source_conflict", full_estimate_requires_vat: true } });
    await render(); await review(); const summary = host.querySelector('[aria-label="متوسط رسوم المنصة"]');
    expect(summary.textContent).toContain("7.47%"); expect(summary.textContent).toContain("نسبة الضريبة الواردة تختلف عن مبالغ المصدر");
    expect(summary.textContent).not.toContain("15%");
});

test("actual variable and fixed fees stay separate and missing splits remain unknown", async () => {
    const result = fixture();
    result.rows[0] = { ...result.rows[0], fee_variable_net: "6.91", fee_fixed_net: "1.00" };
    result.rows[1] = { ...result.rows[1], fee_variable_net: "2.50", fee_fixed_net: "1.50" };
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    const first = host.querySelector('[data-testid="statement-row-matched"]');
    expect(first.textContent).toContain("المتغير قبل الضريبة 6.91"); expect(first.textContent).toContain("الثابت قبل الضريبة 1.00");
    expect(host.querySelector('[data-testid="statement-row-missing"]').textContent).toContain("الثابت قبل الضريبة 1.50");
    expect(host.querySelector('[data-testid="statement-row-different"]').textContent).toContain("الثابت قبل الضريبة غير متاح");
});

test.each(["canonical_salla", "payment_refunds"])("native refund link from %s needs explicit existing-record selection and creates no new amount acknowledgement", async source => {
    const result = fixture(); result.rows[2] = { ...result.rows[2], allowed_actions: ["link_native_refund"],
        native_alias: { id: "native-refund", reference: "SYNTHETIC-NATIVE-REFUND", amount: "50.00", native_currency: "SAR", event_date: stamp, source } };
    api.providerStatementReview.mockResolvedValue(result); api.confirmProviderStatement.mockResolvedValue(confirmation(["different"]));
    await render(); await review();
    expect(host.querySelector('[data-testid="statement-row-different"]').textContent).toContain("ربط مرجع الاسترجاع الوارد بالاسترجاع المحتسب دون خصم إضافي");
    expect(host.textContent).toContain("SYNTHETIC-NATIVE-REFUND"); await check("اختيار العملية 1003");
    expect(host.querySelector('[aria-label="السجل المقابل للعملية 1003"]').value).toBe("");
    await select("السجل المقابل للعملية 1003", "saved-refund"); await check("تأكيد مراجعة العمليات المختارة");
    expect(host.querySelector('[aria-label="تأكيد أن العمليات الجديدة غير مسجلة سابقًا"]')).toBeNull();
    await click("اعتماد العمليات المختارة");
    expect(api.confirmProviderStatement.mock.calls[0][1]).toEqual({ request_id: "intent-1", expected_session_scope: scope, expected_preview_hash: "p".repeat(64),
        decisions: [{ row_id: "different", action: "link_native_refund", existing_id: "saved-refund" }], confirmed: true });
    expect(host.textContent).toContain("حُفظ ربط مرجع الاسترجاع بالعملية المحتسبة دون خصم إضافي");
});

test.each([{ source: "unknown" }, { event_date: null }, { amount: null }, { native_currency: "USD" }])("unbound native alias %j cannot be confirmed", async patch => {
    const result = fixture(); result.rows[2] = { ...result.rows[2], allowed_actions: ["link_native_refund"],
        native_alias: { id: "native-refund", reference: "SYNTHETIC-NATIVE-REFUND", amount: "50.00", native_currency: "SAR", event_date: stamp, source: "payment_refunds", ...patch } };
    api.providerStatementReview.mockResolvedValue(result); await render(); await review();
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull(); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

const openSavedFiles = async () => {
    expect(button("استخدام كشف مرفوع سابقًا")).toBeDefined();
    await click("استخدام كشف مرفوع سابقًا");
};
const selectSavedFile = async () => check("اختيار الكشف المحفوظ saved-tabby.xlsx");

test("saved Tabby bridge reads only on opening and uses the selected original only after an explicit click", async () => {
    api.useSavedProviderStatement.mockResolvedValue({ ...fixture(), statement_average: average });
    await render(); expect(api.providerStatementSavedFiles).not.toHaveBeenCalled();
    await openSavedFiles();
    expect(api.providerStatementSavedFiles).toHaveBeenCalledTimes(1);
    expect(api.providerStatementSavedFiles).toHaveBeenCalledWith({ signal: expect.any(AbortSignal), _mezanAuthDeadlineAt: expect.any(Number) });
    expect(host.textContent).toContain("SAVED-TABBY");
    await selectSavedFile(); await visibility("hidden"); await visibility("visible");
    expect(api.useSavedProviderStatement).not.toHaveBeenCalled(); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
    expect(api.providerStatementSavedFiles).toHaveBeenCalledTimes(1);
    await click("استخدام الكشف المختار ومطابقته");
    expect(api.useSavedProviderStatement).toHaveBeenCalledWith("legacy-tabby-one", {
        request_id: "intent-1", expected_session_scope: scope, expected_started_at: snapshot.started_at, expected_file_hash: savedFile.file_hash,
    }, { signal: expect.any(AbortSignal) });
    expect(host.querySelector('[aria-label="متوسط رسوم المنصة"]').textContent).toContain("7.47%");
    expect(host.querySelector('[data-testid="statement-row-blocked"]').textContent).toContain("غير متاح");
    expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    expect(api.providerStatements).toHaveBeenCalledTimes(1); expect(api.providerStatementSavedFiles).toHaveBeenCalledTimes(1);
});

test("uncertain saved-file use retains the exact file hash, period and request identity for explicit retry", async () => {
    api.useSavedProviderStatement.mockRejectedValueOnce(new Error("uncertain"));
    await render(); await openSavedFiles(); await selectSavedFile(); await click("استخدام الكشف المختار ومطابقته");
    const [id, command] = api.useSavedProviderStatement.mock.calls[0];
    expect(button("إعادة نفس محاولة استخدام الكشف")).toBeDefined();
    await click("إعادة قراءة قائمة الكشوف السابقة");
    expect(button("إعادة نفس محاولة استخدام الكشف")).toBeDefined();
    expect(button("استخدام الكشف المختار ومطابقته").disabled).toBe(true);
    await click("إعادة نفس محاولة استخدام الكشف");
    expect(api.useSavedProviderStatement.mock.calls[1][0]).toBe(id);
    expect(api.useSavedProviderStatement.mock.calls[1][1]).toBe(command); expect(requestId).toHaveBeenCalledTimes(1);
    expect(api.confirmProviderStatement).not.toHaveBeenCalled(); expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test("saved-file metadata with no original remains visibly unavailable and never invents amounts or totals", async () => {
    api.providerStatementSavedFiles.mockResolvedValue(savedFiles({ items: [{ ...savedFile, uploaded_at: null, file_hash: null,
        original_available: false, can_import: false, unavailable_reason: "الملف الأصلي غير محفوظ؛ ارفع نسخة Excel من جديد." }] }));
    await render(); await openSavedFiles();
    const section = host.querySelector('[aria-label="الكشوف المرفوعة سابقًا"]');
    expect(section.textContent).toContain("الملف الأصلي غير محفوظ"); expect(section.textContent).toContain("غير متاح");
    expect(section.textContent).not.toContain("0.00"); expect(section.querySelector("tfoot")).toBeNull();
    expect(section.querySelector('input[type="radio"]').disabled).toBe(true);
    expect(button("استخدام الكشف المختار ومطابقته").disabled).toBe(true);
    await submit('[data-testid="provider-saved-file-use"]'); expect(api.useSavedProviderStatement).not.toHaveBeenCalled();
});

test.each([{ session_scope: "another-owner" }, { started_at: "2026-11-10T00:00:00Z" }])("saved-file scope %j is rejected before selection", async patch => {
    api.providerStatementSavedFiles.mockResolvedValue(savedFiles(patch)); await render(); await openSavedFiles();
    expect(host.textContent).not.toContain("SAVED-TABBY"); expect(api.useSavedProviderStatement).not.toHaveBeenCalled();
    expect(host.textContent).toContain("تغيرت المطابقة أو جلسة التشغيل");
});

test.each([{ file_hash: null }, { original_available: false }])("an importable saved file with unproved original %j is rejected", async patch => {
    api.providerStatementSavedFiles.mockResolvedValue(savedFiles({ items: [{ ...savedFile, ...patch }] }));
    await render(); await openSavedFiles(); expect(host.textContent).not.toContain("SAVED-TABBY");
    expect(api.useSavedProviderStatement).not.toHaveBeenCalled();
});

test("closing saved-file section aborts its read and ignores a late list without automatic restart", async () => {
    const late = deferred(); api.providerStatementSavedFiles.mockReturnValueOnce(late.promise);
    await render(); await openSavedFiles(); const signal = api.providerStatementSavedFiles.mock.calls[0][0].signal;
    await click("إغلاق قائمة الكشوف السابقة"); expect(signal.aborted).toBe(true);
    await act(async () => late.resolve(savedFiles())); await visibility("hidden"); await visibility("visible");
    expect(host.querySelector('[aria-label="الكشوف المرفوعة سابقًا"]')).toBeNull();
    expect(api.providerStatementSavedFiles).toHaveBeenCalledTimes(1); expect(api.useSavedProviderStatement).not.toHaveBeenCalled();
});

test("a refreshed operating period discards the selected saved original until a new explicit list read", async () => {
    await render(); await openSavedFiles(); await selectSavedFile();
    api.providerStatements.mockResolvedValue(history({ started_at: "2026-11-10T00:00:00Z" }));
    await click("قراءة سجل الكشوف");
    expect(host.querySelector('[aria-label="الكشوف المرفوعة سابقًا"] input[type="radio"]')).toBeNull();
    expect(api.providerStatementSavedFiles).toHaveBeenCalledTimes(1); expect(api.useSavedProviderStatement).not.toHaveBeenCalled();
});

test.each(["hidden", "auth_loading", "unmount"])("%s consumes saved-file use and fences its late response", async departure => {
    const late = deferred(); api.useSavedProviderStatement.mockReturnValueOnce(late.promise);
    await render(); await openSavedFiles(); await selectSavedFile(); await click("استخدام الكشف المختار ومطابقته");
    const signal = api.useSavedProviderStatement.mock.calls[0][2].signal;
    if (departure === "hidden") await visibility("hidden");
    else if (departure === "auth_loading") { useOptionalAuth.mockReturnValue({ user: { id: "owner-one", role: "owner" }, loading: true }); await render(); }
    else { await act(async () => root.unmount()); mounted = false; }
    expect(signal.aborted).toBe(true);
    await act(async () => late.resolve({ ...fixture(), statement_average: average }));
    if (departure === "hidden") await visibility("visible");
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull();
    expect(api.useSavedProviderStatement).toHaveBeenCalledTimes(1); expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    expect(api.providerStatements).toHaveBeenCalledTimes(1); expect(api.providerStatementSavedFiles).toHaveBeenCalledTimes(1);
});

test.each([{ active: false }, { permissions: { upload: false } }])("saved-file restriction %j allows metadata but no use", async patch => {
    api.providerStatementSavedFiles.mockResolvedValue(savedFiles(patch)); await render(); await openSavedFiles();
    expect(host.textContent).toContain("SAVED-TABBY"); expect(host.querySelector('[aria-label="اختيار الكشف المحفوظ saved-tabby.xlsx"]').disabled).toBe(true);
    await submit('[data-testid="provider-saved-file-use"]'); expect(api.useSavedProviderStatement).not.toHaveBeenCalled();
});

test.each(['upload', 'history'])('directory bookmark opens the %s section after the original single read', async section => {
    const previous = window.location.href;
    try {
        window.history.replaceState(null, '', '/operational-balances/provider-statements#' + section);
        await render();
        expect(document.activeElement.id).toBe(section);
        expect(api.providerStatements).toHaveBeenCalledTimes(1);
        expect(api.providerStatementReview).not.toHaveBeenCalled();
        expect(api.providerStatementSavedFiles).not.toHaveBeenCalled();
        expect(api.uploadProviderStatement).not.toHaveBeenCalled();
        expect(api.confirmProviderStatement).not.toHaveBeenCalled();
    } finally { window.history.replaceState(null, '', previous); }
});

test.each(["read","write"])("the configured employee statement mode %s uses authorized history and limits upload controls", async mode => {
    const user = { id:"employee", role:"employee", web_page_access:{pages:[{
        id:"balances.statements", to:"/operational-balances/provider-statements", section:"balances",mode,
    }]} };
    useOptionalAuth.mockReturnValue({user,loading:false,authStatus:"authenticated"});
    await render();
    expect(api.providerStatements).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="provider-statement-upload"]') !== null).toBe(mode === "write");
    expect(host.querySelector('a[href="/operational-balances/financial-position?view=providers"]')).toBeNull();
    expect(api.uploadProviderStatement).not.toHaveBeenCalled();
    expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

test.each(['statements-only','statements-and-assets','owner'])('provider statement ledger return respects %s navigation grants', async access => {
    if (access !== 'owner') {
        const pages = [{id:'balances.statements',to:'/operational-balances/provider-statements',section:'balances',mode:'read'}];
        if (access === 'statements-and-assets') pages.push({id:'balances.assets',to:'/operational-balances?view=assets',section:'balances',mode:'read'});
        useOptionalAuth.mockReturnValue({user:{id:'employee',role:'employee',web_page_access:{pages}},loading:false,authStatus:'authenticated'});
    }
    await render({providerId:'tabby'});
    const link = host.querySelector('a[href="/operational-balances?view=ledger&kind=provider&id=tabby"]');
    expect(Boolean(link)).toBe(access !== 'statements-only');
    expect(host.querySelector('a[href="/operational-balances/provider-statements"]')).not.toBeNull();
    expect(api.providerStatements).toHaveBeenCalledTimes(1);
    expect(api.providerStatementReview).not.toHaveBeenCalled();
    expect(api.uploadProviderStatement).not.toHaveBeenCalled();
    expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});


test("provider context filters saved and uploaded lists without overriding detected file provider", async () => {
    api.providerStatements.mockResolvedValue(history({items:[{...statement,counts:{...counts},status:"needs_review"},
        {...statement,id:"tamara-file",provider:"tamara",filename:"tamara-only.xlsx",counts:{...counts},status:"needs_review"}]}));
    api.providerStatementSavedFiles.mockResolvedValue(savedFiles({items:[savedFile,{...savedFile,id:"tamara-saved",provider:"tamara",filename:"tamara-saved.xlsx"}]}));
    await render({providerId:"tabby"});
    expect(host.querySelector('h1').textContent).toContain('تابي');
    expect(host.textContent).not.toContain('tamara-only.xlsx');
    await click("استخدام كشف مرفوع سابقًا");
    expect(host.textContent).toContain("saved-tabby.xlsx");
    expect(host.textContent).not.toContain("tamara-saved.xlsx");
    api.uploadProviderStatement.mockResolvedValue(fixture("tamara"));
    await choose(); await submit('[data-testid="provider-statement-upload"]');
    expect(host.querySelector('[role="alert"]').textContent).toContain('الملف الذي تمت قراءته يخص تمارا');
    expect(host.querySelector('.op-statement-confirm').disabled).toBe(true);
    expect(api.uploadProviderStatement.mock.calls[0][1]).not.toHaveProperty('provider');
    await submit('[data-testid="provider-statement-review"] form');
    expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});

test.each(['emkan','paypal','unknown'])('unsupported provider %s has no invented importer', async providerId => {
    await render({providerId});
    expect(host.textContent).toContain('لا يوجد مستورد معتمد');
    expect(host.querySelector('input[type=file]')).toBeNull();
    expect(api.providerStatements).not.toHaveBeenCalled();
    expect(api.uploadProviderStatement).not.toHaveBeenCalled();
});

test('changing provider discards the previous review and cannot admit its late response', async () => {
    const pendingRead=deferred(); api.providerStatementReview.mockReturnValueOnce(pendingRead.promise);
    await render({providerId:'tabby'}); await review();
    const signal=api.providerStatementReview.mock.calls[0][1].signal;
    await render({providerId:'tamara'});
    expect(signal.aborted).toBe(true);
    await act(async()=>pendingRead.resolve(fixture('tabby')));
    expect(host.querySelector('[data-testid="provider-statement-review"]')).toBeNull();
    expect(api.confirmProviderStatement).not.toHaveBeenCalled();
});
