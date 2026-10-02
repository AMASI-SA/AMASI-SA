import React, { act } from "react";
import { createRoot } from "react-dom/client";
import RichShippingContracts, { shippingTerms } from "./RichShippingContracts";
const permissions = ["accounting.shipping.view", "accounting.rules.manage", "accounting.shipping.contracts.review"];
const terms = { courier_id: "courier-a", shipping_cost: "20", payment_mode: "postpaid", shipping_cost_vat_inclusive: false, shipping_vat_percent: "15", commission_vat_inclusive: true, commission_vat_percent: "15", effective_from: "2026-10-01T00:00", effective_to: "", evidence_ref: "retained-contract", source_kind: "contract", cod_fee_tiers: [{ min_amount: "0", max_amount: "", min_inclusive: true, max_inclusive: false, commission_percent: "0.01", fixed_fee: "2" }], settlement_bank_id: "must-not-send", opening_payable: "123", opening_cod_receivable: "456" };
const snapshot = () => ({ version: 7, couriers: [{ courier_key: "courier-a", name: "Canonical carrier", status: "active" }, { courier_key: "inactive", name: "Inactive", status: "inactive" }], drafts: [{ id: "draft-1", hash: "a".repeat(64), status: "draft", context: "delivery", terms }], contract_evidence: [{ evidence_id: "proof-1", file_id: "file-1", courier_id: "courier-a", purpose: "contract", revision: 1, state: "approved" }], contracts: [] });
let container, root, transport;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    transport = { getRichShippingContracts: jest.fn().mockResolvedValue(snapshot()), saveRichShippingDraft: jest.fn().mockResolvedValue({}), reviewShippingEvidence: jest.fn().mockResolvedValue({}), revokeShippingEvidence: jest.fn().mockResolvedValue({}), approveRichShippingContract: jest.fn().mockResolvedValue({}), downloadShippingEvidence: jest.fn().mockResolvedValue(undefined), uploadOnboardingEvidence: jest.fn().mockResolvedValue({ source_file_id: "retained-1" }) };
});
afterEach(() => { act(() => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const field = label => container.querySelector(`[aria-label="${label}"]`);
const button = text => [...container.querySelectorAll("button")].find(b => b.textContent === text);
async function render(props = {}) { await act(async () => root.render(<RichShippingContracts transport={transport} permissions={permissions} financialBase="/api/financial-provider-apps/accounting-module/financial-accounts" value={{ "courier-a": terms }} onChange={() => {}} {...props} />)); }
function change(label, value) { const target = field(label); act(() => { Object.getOwnPropertyDescriptor(target.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(target, value); target.dispatchEvent(new Event(target.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); }
function confirm() { change("سبب إجراء العقد", "checked retained original"); act(() => field("تأكيد إجراء العقد").click()); }
async function click(text) { await act(async () => button(text).click()); }

test("terms preserve decimal fraction and Riyadh instant without opening balances or bank", () => {
    const result = shippingTerms("courier-a", terms);
    expect(result.cod_fee_tiers[0]).toEqual({ min_amount: "0", max_amount: null, min_inclusive: true, max_inclusive: false, commission_percent: "0.01", fixed_fee: "2", vat_percent: "15", vat_included: true });
    expect(result.effective_from).toBe("2026-10-01T00:00:00+03:00");
    expect(result.effective_to).toBeNull();
    expect(result).not.toHaveProperty("settlement_bank_id"); expect(result).not.toHaveProperty("opening_payable"); expect(result).not.toHaveProperty("opening_cod_receivable");
});
test("canonical registry and explicit confirmation save native rich terms only", async () => {
    await render();
    expect([...field("شركة الشحن").options].map(o => o.value)).toEqual(["", "courier-a"]);
    change("شركة الشحن", "courier-a"); expect(button("حفظ مسودة الشركة").disabled).toBe(true);
    confirm(); await click("حفظ مسودة الشركة");
    expect(transport.saveRichShippingDraft).toHaveBeenCalledTimes(1);
    const payload = transport.saveRichShippingDraft.mock.calls[0][0];
    expect(payload).toEqual({ request_id: expect.any(String), version: 7, confirmed: true, reason: "checked retained original", context: "delivery", terms: shippingTerms("courier-a", terms) });
    expect(payload.request_id.length).toBeGreaterThanOrEqual(8);
});
test("uncertain mutation retries exact payload and prevents replacement request", async () => {
    transport.saveRichShippingDraft.mockRejectedValueOnce(new Error("response lost"));
    const busy = jest.fn(); await render({ onBusyChange: busy }); change("شركة الشحن", "courier-a"); confirm();
    await click("حفظ مسودة الشركة"); expect(busy).toHaveBeenLastCalledWith(true);
    expect(button("حفظ مسودة الشركة").disabled).toBe(true);
    await click("إعادة الطلب نفسه");
    expect(transport.saveRichShippingDraft.mock.calls[1][0]).toEqual(transport.saveRichShippingDraft.mock.calls[0][0]);
    expect(busy).toHaveBeenLastCalledWith(false);
});
test("version conflict refreshes and requires renewed confirmation", async () => {
    transport.saveRichShippingDraft.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: "shipping_setup_version_conflict" } } } });
    await render(); change("شركة الشحن", "courier-a"); confirm(); await click("حفظ مسودة الشركة");
    expect(transport.getRichShippingContracts).toHaveBeenCalledTimes(2);
    expect(field("تأكيد إجراء العقد").checked).toBe(false);
    expect(button("إعادة الطلب نفسه")).toBeUndefined();
    expect(container.textContent).toContain("shipping_setup_version_conflict");
});
test("review needs explicit permission, original download and exact selected purpose", async () => {
    await render(); confirm(); change("معرف الملف المحفوظ", "file-1"); change("شركة دليل العقد", "courier-a"); change("غرض دليل العقد", "shipping_tax");
    expect(button("راجعت الأصل وأعتمد هذا الدليل").disabled).toBe(true);
    await click("تنزيل الأصل المحفوظ للمراجعة"); await click("راجعت الأصل وأعتمد هذا الدليل");
    expect(transport.downloadShippingEvidence).toHaveBeenCalledWith("file-1");
    expect(transport.reviewShippingEvidence).toHaveBeenCalledWith(expect.objectContaining({ file_id: "file-1", courier_id: "courier-a", purpose: "shipping_tax", confirmation: "APPROVE_MZ2_SHIPPING_EVIDENCE" }));
    await render({ permissions: permissions.filter(p => !p.endsWith(".review")) });
    expect(button("راجعت الأصل وأعتمد هذا الدليل").disabled).toBe(true);
});
test("approval uses server draft hash and evidence; revoke uses current evidence revision", async () => {
    await render(); change("مسودة العقد", "draft-1"); change("دليل العقد", "proof-1"); confirm();
    await click("اعتماد شروط العقد المختار");
    expect(transport.approveRichShippingContract).toHaveBeenCalledWith(expect.objectContaining({ draft_id: "draft-1", draft_hash: "a".repeat(64), contract_evidence_id: "proof-1", confirmation: "APPROVE_MZ2_SHIPPING_CONTRACT" }));
    act(() => field("تأكيد إجراء العقد").click()); await click("سحب اعتماد الدليل proof-1");
    expect(transport.revokeShippingEvidence).toHaveBeenCalledWith(expect.objectContaining({ evidence_id: "proof-1", revision: 1, confirmation: "REVOKE_MZ2_SHIPPING_EVIDENCE" }));
});

test("missing source kind is not defaulted even when all other terms are present", async () => {
    await render({ value: { "courier-a": { ...terms, source_kind: "" } } });
    change("شركة الشحن", "courier-a"); confirm(); await click("حفظ مسودة الشركة");
    expect(transport.saveRichShippingDraft).not.toHaveBeenCalled();
    expect(container.textContent).toContain("حدد نوع المصدر صراحة");
});

test("failed retained original download cannot authorize review", async () => {
    transport.downloadShippingEvidence.mockRejectedValueOnce({ response: { status: 403, data: { detail: { code: "permission_denied" } } } });
    await render(); confirm(); change("معرف الملف المحفوظ", "file-1"); change("شركة دليل العقد", "courier-a"); change("غرض دليل العقد", "contract");
    await click("تنزيل الأصل المحفوظ للمراجعة");
    expect(button("راجعت الأصل وأعتمد هذا الدليل").disabled).toBe(true);
    expect(container.textContent).toContain("permission_denied");
    expect(transport.reviewShippingEvidence).not.toHaveBeenCalled();
});

test("accountant reads saved server terms and approved terms without raw JSON", async () => {
    const state = snapshot();
    state.drafts[0].terms = { ...terms, shipping_cost: "37.50", payment_mode: "prepaid", effective_from: "2026-09-30T21:00:00Z", effective_to: "2026-10-31T21:00:00Z" };
    state.drafts[0].created_by = "accountant-1";
    state.contract_evidence[0].approved_by = "reviewer-1";
    state.contracts = [{ id: "approved-contract", party_id: "courier-a", status: "approved", draft_id: "prior-draft", draft_hash: "b".repeat(64), confirmed_by: "reviewer-2", contract_version: state.drafts[0].terms, evidence_snapshot: { items: state.contract_evidence } }];
    transport.getRichShippingContracts.mockResolvedValue(state);
    await render(); change("مسودة العقد", "draft-1"); change("دليل العقد", "proof-1");
    const summary = field("شروط المسودة المحفوظة");
    expect(summary.textContent).toContain("37.50 ريال سعودي");
    expect(summary.textContent).toContain("مسبق الدفع");
    expect(summary.textContent).toContain("15% · غير شامل الضريبة");
    expect(summary.textContent).toContain("15% · شامل الضريبة");
    expect(summary.textContent).toContain("2026-10-01 00:00");
    expect(summary.textContent).toContain("2026-11-01 00:00");
    expect(summary.querySelector("table").textContent).toContain("1% (الكسر المحفوظ: 0.01)");
    expect(summary.querySelector("table").textContent).toContain("بلا حد أعلى");
    expect(summary.querySelector("table").textContent).toContain("2 ريال سعودي");
    expect(field("شروط العقد المعتمد").textContent).toContain("37.50 ريال سعودي");
    expect(container.querySelector("pre")).toBeNull();
    const audit = [...container.querySelectorAll("details")].find(item => item.textContent.includes("accountant-1"));
    expect(audit.textContent).toContain("draft-1");
    expect(audit.textContent).toContain("a".repeat(64));
    expect(audit.textContent).toContain("reviewer-1");
    expect(audit.textContent).toContain("file-1");
    expect(container.textContent).not.toContain("opening_balance");
    expect(container.textContent).not.toContain("accounting.shipping");
    expect(container.textContent).not.toContain("P02");
});
