import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OnboardingSsotSetup, { contractSection } from "./OnboardingSsotSetup";
import AccountingOnboarding, { FINANCIAL_SECTIONS } from "./AccountingOnboarding";

const clone = value => JSON.parse(JSON.stringify(value));
const makeSession = () => ({ id: "ssot-session", version: 1, schema_version: 1, existing: false, status: "draft",
    cutover: { cutover_at: "2026-10-01T00:00:00+03:00", cutover_timezone: "Asia/Riyadh", cutover_evidence_file_id: "cutover-proof" },
    sections: Object.fromEntries(FINANCIAL_SECTIONS.map(id => [id, { status: "incomplete", reason: "section-note", evidence_file_id: id + "-proof", data: { lines: [] } }])) });
const fact = (category, amount = "100.00") => ({ id: category + "-contract", entity_id: category + "-identity", category,
    amount, currency: "SAR", evidence: "equity-proof", display_name: category, cutover_date: "2026-10-01" });
const paid = { invoice_id: "invoice-exact", obligation_id: "obligation-exact", title: "اشتراك سلة", type: "subscription",
    entity_name: "Salla", coverage_start: "2026-08-21", coverage_end: "2027-08-21", payment_date: "2026-08-21",
    payment_amount: "3660.00", currency: "SAR", auto_renew: true, evidence: "invoice-proof",
    calculation: { eligible: true, consumed_before_cutover: "410.00", remaining_prepaid_after_cutover: "3250.00" } };
const selection = { id: "prepaid-exact", entity_id: "prepaid-exact", category: "prepaid_expense", currency: "SAR", evidence: "equity-proof", calculation: paid.calculation };
const fee = { id: "fee-exact", provider: "salla", percentage: "2.00", fixed_amount: "1.00", currency: "SAR", effective_from: "2026-01-01", effective_to: null };
let root, node, transport, onSelect, onError;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    let requestId = 0;
    Object.defineProperty(global, "crypto", { configurable: true, value: { randomUUID: () => `ssot-request-${++requestId}` } });
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    onSelect = jest.fn(async () => {}); onError = jest.fn();
    transport = { listOnboardingFeePolicies: jest.fn(async () => ({ items: [fee] })), listOnboardingFacts: jest.fn(async () => ({ items: [] })),
        listOnboardingPrepaids: jest.fn(async () => ({ items: [paid] })), selectOnboardingPrepaid: jest.fn(async () => selection),
        createOnboardingFact: jest.fn(async payload => ({ ...fact(payload.category), ...payload })), createOnboardingFeePolicy: jest.fn(async () => fee) };
});
afterEach(() => { act(() => root.unmount()); node.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
async function show(stage, session = makeSession(), extras = {}) {
    await act(async () => root.render(<OnboardingSsotSetup stage={stage} session={session} transport={transport} onSelect={onSelect} onError={onError} {...extras} />));
}
async function set(label, next) { await act(async () => { const e = node.querySelector(`[aria-label="${label}"]`); Object.getOwnPropertyDescriptor(e.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(e, next); e.dispatchEvent(new Event(e.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); }
const button = text => [...node.querySelectorAll("button")].find(item => item.textContent === text);
async function click(text) { await act(async () => button(text).click()); }


test("contract selection preserves sibling financial lines, binding evidence and existing contract references", () => {
    const session = makeSession();
    session.sections.providers.data = { lines: [{ category: "provider_receivable", entity_id: "salla", original_amount: "50" }], provider_bindings: [{ provider: "salla", bank_id: "native-bank" }], fee_policy_ids: ["prior-policy"] };
    session.sections.equity.data = { lines: [{ category: "other_payable", entity_id: "sibling", original_amount: "19" }], typed_fact_ids: ["sibling-fact"], prepaid_selection_ids: ["prior-prepaid"] };
    const before = clone(session);
    const policySection = contractSection(session, "payment_fees", fee);
    expect(policySection).toMatchObject({ status: "incomplete", reason: "section-note", evidence_file_id: "providers-proof", data: { ...before.sections.providers.data, fee_policy_ids: ["prior-policy", "fee-exact"] } });
    const equity = contractSection(session, "prepaid", selection);
    expect(equity.data.lines[0]).toEqual(before.sections.equity.data.lines[0]);
    expect(equity.data.prepaid_selection_ids).toEqual(["prior-prepaid", "prepaid-exact"]);
    expect(equity.data.typed_fact_ids).toEqual(["sibling-fact"]);
    expect(equity.data.lines[1]).toMatchObject({ entity_id: "prepaid-exact", category: "prepaid_expense", original_amount: "3250.00", meaning: "available_to_us" });
    expect(session).toEqual(before);
});

test("sales VAT and input VAT remain separate identities, amounts, evidence and sides without netting", () => {
    const session = makeSession();
    session.sections.equity = contractSection(session, "obligations", fact("sales_vat_payable", "300.00"));
    session.sections.equity = contractSection(session, "obligations", fact("input_vat", "120.00"));
    const data = contractSection(session, "obligations", fact("input_vat", "120.00")).data;
    expect(data.typed_fact_ids).toEqual(["sales_vat_payable-contract", "input_vat-contract"]);
    expect(data.lines).toEqual([
        expect.objectContaining({ category: "sales_vat_payable", original_amount: "300.00", meaning: "owed_by_us", evidence_file_id: "equity-proof" }),
        expect.objectContaining({ category: "input_vat", original_amount: "120.00", meaning: "available_to_us", evidence_file_id: "equity-proof" }),
    ]);
});

test("explicit zero remains a documented line instead of becoming missing or netted", () => {
    expect(contractSection(makeSession(), "obligations", fact("other_payable", "0.00")).data.lines[0])
        .toMatchObject({ original_amount: "0.00", meaning: "zero", evidence_file_id: "equity-proof" });
});

test("existing provider policy is selected without creating another policy", async () => {
    await show("payment_fees");
    expect(node.textContent).toContain("salla");
    await click("اختيار العقد المحفوظ");
    expect(onSelect).toHaveBeenCalledWith(fee);
    expect(transport.createOnboardingFeePolicy).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
});

test("prepaid UI displays payment, coverage, calendar result and selects exact persisted obligation/invoice", async () => {
    await show("prepaid");
    for (const value of ["اشتراك سلة", "2026-08-21", "2027-08-21", "3660.00", "410.00", "3250.00", "invoice-proof"]) expect(node.textContent).toContain(value);
    expect(transport.listOnboardingPrepaids).toHaveBeenCalledWith("2026-10-01");
    await click("اختيار الالتزام وحفظ الرصيد");
    expect(transport.selectOnboardingPrepaid).toHaveBeenCalledWith({ obligation_id: "obligation-exact", invoice_id: "invoice-exact", cutover_date: "2026-10-01", currency: "SAR", evidence: "equity-proof" });
    expect(onSelect).toHaveBeenCalledWith(selection);
});

test.each(["unpaid", "coverage_ended", "stale"])("%s prepaid candidate cannot be selected", async reason => {
    transport.listOnboardingPrepaids.mockResolvedValue({ items: [{ ...paid, source_stale: reason === "stale", payment_date: reason === "unpaid" ? null : paid.payment_date, calculation: { ...paid.calculation, eligible: reason === "stale", reason } }] });
    await show("prepaid");
    expect(button("اختيار الالتزام وحفظ الرصيد").disabled).toBe(true);
    await click("اختيار الالتزام وحفظ الرصيد");
    expect(transport.selectOnboardingPrepaid).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
});

test("refresh uses saved prepaid identity rather than creating a replacement", async () => {
    transport.listOnboardingPrepaids.mockResolvedValue({ items: [{ ...paid, selection }] });
    const session = makeSession(); session.sections.equity = contractSection(session, "prepaid", selection);
    await show("prepaid", session);
    await act(async () => root.unmount()); root = createRoot(node);
    await show("prepaid", { ...clone(session), version: 2 });
    await click("اختيار الالتزام وحفظ الرصيد");
    expect(onSelect).toHaveBeenCalledWith(selection);
    expect(transport.selectOnboardingPrepaid).not.toHaveBeenCalled();
    expect(contractSection(session, "prepaid", selection).data.prepaid_selection_ids).toEqual(["prepaid-exact"]);
});

test("typed fact creation sends its exact category, cutover and saved section evidence", async () => {
    await show("obligations");
    await set("عقد التصنيف", "input_vat"); await set("اسم الجهة أو الالتزام", "VAT invoice"); await set("مرجع العقد", "invoice-reference"); await set("المبلغ الموثق", "120.00");
    await click("إنشاء العقد وحفظ اختياره");
    expect(transport.createOnboardingFact).toHaveBeenCalledWith({ category: "input_vat", display_name: "VAT invoice", reference: "invoice-reference", amount: "120.00", currency: "SAR", cutover_date: "2026-10-01", evidence: "equity-proof" });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ category: "input_vat", amount: "120.00" }));
});

test("contract creation stops before mutation when saved evidence is absent", async () => {
    const session = makeSession(); session.sections.providers.evidence_file_id = null;
    await show("payment_fees", session); await click("إنشاء العقد وحفظ اختياره");
    expect(transport.createOnboardingFeePolicy).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "opening_evidence_section_file_required" }));
});

function integrated() {
    let saved = makeSession();
    Object.assign(transport, {
        getOnboardingDefinitions: jest.fn(async () => ({ schema_version: 1, ssot_setup_version: 1, financial_base: "/api/financial-provider-apps/accounting-module/financial-accounts", sections: FINANCIAL_SECTIONS, opening_categories: {} })),
        listOnboardingSessions: jest.fn(async () => ({ items: [clone(saved)] })), getOnboardingSession: jest.fn(async () => clone(saved)),
        getOnboardingFinancialAccounts: jest.fn(async () => ({ items: [] })), getOnboardingIdentities: jest.fn(async () => ({ items: [] })),
        saveOnboardingSection: jest.fn(async (id, section, body) => { saved = { ...saved, version: saved.version + 1, sections: { ...saved.sections, [section]: clone(body) } }; return clone(saved); }),
    });
    return () => clone(saved);
}
async function showIntegrated() {
    await act(async () => root.render(<AccountingOnboarding transport={transport} accountingPermissions={["accounting.opening_balances.view", "accounting.opening_balances.drafts.manage"]} />));
    await set("الجلسات المحفوظة", "ssot-session"); await click("استعادة المحفوظ وتجاهل التعديلات المحلية");
    await act(async () => [...node.querySelectorAll("nav button")].find(item => item.textContent.includes("عمولات طرق الدفع والضرائب")).click());
}

test("central integration preserves policy selection across reload and disables selection for dirty financial metadata", async () => {
    const peek = integrated(); await showIntegrated();
    await click("اختيار العقد المحفوظ");
    expect(peek().sections.providers.data.fee_policy_ids).toEqual(["fee-exact"]);
    await click("استعادة المحفوظ وتجاهل التعديلات المحلية");
    expect(node.querySelector('[data-testid="ssot-setup"]')).not.toBeNull();
    await set("سبب القسم المالي", "unsaved edits");
    expect(node.querySelector('[data-testid="ssot-setup"]').disabled).toBe(true);
    expect(transport.saveOnboardingSection).toHaveBeenCalledTimes(1);
});

test("central session controls stay locked while saving an existing contract", async () => {
    integrated(); await showIntegrated();
    let finish;
    transport.saveOnboardingSection.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    await click("اختيار العقد المحفوظ");
    expect(node.querySelector('[aria-label="الجلسات المحفوظة"]').disabled).toBe(true);
    expect(button("حفظ البيانات المالية").closest("fieldset").disabled).toBe(true);
    await act(async () => finish({ ...makeSession(), version: 2 }));
});


test("late catalog response from an older session cannot overwrite current selections", async () => {
    let finishOld;
    transport.listOnboardingFeePolicies.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    await show("payment_fees");
    transport.listOnboardingFeePolicies.mockResolvedValue({ items: [{ ...fee, id: "current-policy", provider: "tamara" }] });
    await show("payment_fees", { ...makeSession(), id: "different-session", version: 2 });
    await act(async () => finishOld({ items: [{ ...fee, id: "stale-policy", provider: "old-provider" }] }));
    expect(node.querySelector("ul").textContent).toContain("tamara");
    expect(node.querySelector("ul").textContent).not.toContain("old-provider");
    await click("اختيار العقد المحفوظ");
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "current-policy" }));
});

test("reselecting a foreign-currency fact preserves its stored FX evidence and allows an explicit replacement", () => {
    const session = makeSession();
    const foreign = { ...fact("accrued_expense"), currency: "USD" };
    const fx = { fx_rate_to_sar: "3.75", fx_at: "2026-10-01T00:00:00+03:00", fx_source: "bank", fx_evidence_file_id: "immutable-fx-proof" };
    session.sections.equity = contractSection(session, "obligations", { ...foreign, fx_fields: fx });
    const reused = contractSection(session, "obligations", foreign);
    expect(reused.data.lines[0]).toMatchObject({ ...fx, original_currency: "USD", original_amount: "100.00" });
    const replacement = contractSection(session, "obligations", { ...foreign, fx_fields: { ...fx, fx_rate_to_sar: "3.76", fx_evidence_file_id: "replacement-proof" } });
    expect(replacement.data.lines[0]).toMatchObject({ fx_rate_to_sar: "3.76", fx_evidence_file_id: "replacement-proof" });
    expect(replacement.data.lines).toHaveLength(1);
    expect(replacement.data.typed_fact_ids).toEqual(["accrued_expense-contract"]);
});

test("foreign-currency selection sends explicit FX time with Riyadh timezone and evidence", async () => {
    transport.listOnboardingFacts.mockResolvedValue({ items: [{ ...fact("other_payable"), currency: "USD" }] });
    await show("obligations");
    await set("سعر التحويل إلى SAR", "3.75"); await set("وقت سعر التحويل بالرياض", "2026-10-01T00:00");
    await set("مصدر سعر التحويل", "Bank statement"); await set("معرف ملف دليل سعر التحويل", "fx-proof-id");
    await click("اختيار العقد المحفوظ");
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ currency: "USD", fx_fields: { fx_rate_to_sar: "3.75", fx_at: "2026-10-01T00:00:00+03:00", fx_source: "Bank statement", fx_evidence_file_id: "fx-proof-id" } }));
});
