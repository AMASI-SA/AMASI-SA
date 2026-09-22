import React, {act} from "react";
import {createRoot} from "react-dom/client";
import AdvertisingAccountsDraft from "./AdvertisingAccountsDraft";
import {MODEL_VERSION, PREFIX, PERMISSION_KEYS} from "./advertisingAccountsDraftContract";

const owner = "synthetic-owner";
const link = "synthetic-link";
const scope = {owner_id: owner, linked_account_ref: link};
const row = (values) => ({...scope, ...values});
function fixture() {
    return {
        version: MODEL_VERSION, owner_id: owner, status: "ready",
        accounts: [row({ad_provider: "snapchat", external_account_id: "SYN-EXT-1", name: "حساب اختباري فقط", currency: "SAR", timezone_name: "Asia/Riyadh",
            payment_mode: "postpaid", accrual_source: "daily_spend", funding_ref: "sibling-payable", default_source_ref: "default-bank",
            data_status: "DATA_COMPLETE", accounting_status: "DRAFT_CREATED"})],
        summary: {today: {status: "complete", value: "100.00"}, month: {status: "complete", value: "400.00"}, wallets: {status: "complete", value: "25.00"},
            debts: {status: "complete", value: "999999.99"}, paid_remaining: {status: "complete", paid_sar: "40.00", remaining_sar: "60.00"},
            overdue: {status: "complete", value: 1}, unclosed: {status: "complete", value: 2}, unlinked: {status: "complete", value: 0}, unmatched: {status: "complete", value: 3}},
        details: {[link]: {...scope,
            daily: [row({date: "2026-09-21", original_amount: "100.00", currency: "SAR", source_already_sar: true, fx_rate: "1", value_sar: "100.00", fx_source: "SYN-FX-SOURCE", settings_revision: "frozen-v1", source_revision: "2", status: "DRAFT_CREATED"})],
            invoices: [row({external_number: "SYN-INVOICE-1", original_amount: "100.00", currency: "SAR", value_sar: "100.00", paid_sar: "0.00", remaining_sar: "100.00", provider_status: "paid", status: "unpaid", evidence_ref: "SYN-PDF", source: "upload"})],
            payments: [row({reference: "SYN-PAYMENT-1", source_ref: "actual-cash-not-default", movement_ref: "SYN-MOVEMENT", evidence_ref: "SYN-PROOF", principal_sar: "40.00", fee_sar: "2.00", fx_difference_sar: "0.00"})],
            allocations: [row({payment_ref: "SYN-PAYMENT-1", invoice_ref: "SYN-INVOICE-1", original_amount: "40.00", currency: "SAR", carrying_sar: "40.00", remaining_sar: "60.00"})],
            reconciliation: [row({reference: "SYN-MATCH", daily_sar: "10000.00", invoice_sar: "10050.00", difference_sar: "50.00", status: "NEEDS_REVIEW"})],
            journal_legs: [row({preview_ref: "SYN-PREVIEW", role: "bank_fee_expense", reference: "shared-fee-account", debit: "2.00", credit: "0.00"})],
            topups: [], attachments: [], audit: [],
        }},
    };
}
let host, root;
beforeAll(() => {global.IS_REACT_ACT_ENVIRONMENT = true;});
afterAll(() => {delete global.IS_REACT_ACT_ENVIRONMENT;});
beforeEach(() => {host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);});
afterEach(() => {act(() => root.unmount()); host.remove();});
function render(overrides = {}) {act(() => root.render(<AdvertisingAccountsDraft ownerId={owner} permissions={PERMISSION_KEYS} model={fixture()} {...overrides}/>));}
function click(element) {act(() => element.dispatchEvent(new MouseEvent("click", {bubbles: true})));}
function openAccount() {click(Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "حساب اختباري فقط"));}
function tab(id) {click(host.querySelector(`[data-tab="${id}"]`));}

test("view permission fails closed", () => {render({permissions: []}); expect(host.textContent).toContain("لا تملك صلاحية"); expect(host.querySelector("table")).toBeNull();});
test("missing adapter shows unknowns, not invented accounts or zero", () => {
    render({model: null}); expect(host.querySelectorAll("[data-card]")).toHaveLength(9);
    expect(host.textContent).toContain("لم تُنشأ حسابات تجريبية"); expect(host.textContent).not.toContain("0.00");
    expect(host.querySelector("input").disabled).toBe(true);
});
test("nine cards, thirteen account columns and English exact amounts", () => {
    render(); expect(host.querySelectorAll("[data-card]")).toHaveLength(9);
    expect(host.querySelectorAll('[data-testid="ad-accounts-table"] th')).toHaveLength(13);
    expect(host.querySelector('[data-card="debts"]').textContent).toContain("999,999.99");
    expect(host.querySelector('[data-card="wallets"]').textContent).toContain("25.00");
});
test("four platforms come only from supplied linked rows", () => {
    const model = fixture(); model.accounts = ["snapchat", "meta", "tiktok", "google"].map((provider, index) => ({...model.accounts[0], ad_provider: provider, linked_account_ref: `syn-${index}`, external_account_id: `ext-${index}`}));
    render({model}); expect(host.querySelectorAll('[data-testid="ad-accounts-table"] tbody tr')).toHaveLength(4);
    expect(host.querySelector('input[name="external_account_id"]')).toBeNull();
});
test("selecting a linked account exposes eight tabs", () => {render(); openAccount(); expect(host.querySelectorAll('[role="tab"]')).toHaveLength(8); expect(host.querySelector('[data-tab="overview"]').getAttribute("aria-selected")).toBe("true");});
test("daily tab retains FX source and frozen version", () => {render(); openAccount(); tab("daily"); const panel = host.querySelector('[role="tabpanel"]'); expect(panel.textContent).toContain("SYN-FX-SOURCE"); expect(panel.textContent).toContain("frozen-v1");});
test("invoice display never derives payment from API flag", () => {render(); openAccount(); tab("invoices"); const panel = host.querySelector('[role="tabpanel"]'); expect(panel.textContent).toContain("غير مدفوعة"); expect(panel.textContent).not.toContain("مدفوعة بعد المطابقة"); expect(panel.textContent).toContain("SYN-PDF");});
test("payment tab shows actual bank and allocation residual", () => {render(); openAccount(); tab("payments"); const panel = host.querySelector('[role="tabpanel"]'); expect(panel.textContent).toContain("actual-cash-not-default"); expect(panel.textContent).toContain("SYN-MOVEMENT"); expect(panel.textContent).toContain("SYN-PROOF"); expect(panel.textContent).toContain("60.00");});
test("invoice difference stays visible and is not hidden as zero", () => {render(); openAccount(); tab("reconciliation"); const panel = host.querySelector('[role="tabpanel"]'); expect(panel.textContent).toContain("10,050.00"); expect(panel.textContent).toContain("50.00"); expect(panel.textContent).toContain("تحتاج مراجعة");});
test("journal preview separates fee and has no posting control", () => {render(); openAccount(); tab("journals"); const panel = host.querySelector('[role="tabpanel"]'); expect(panel.textContent).toContain("مصروف عمولات بنكية"); expect(panel.querySelectorAll("button")).toHaveLength(0);});
test("foreign-owner detail rows are not rendered", () => {
    const model = fixture(); model.details[link].invoices[0].owner_id = "other-owner"; model.details[link].invoices[0].external_number = "SECRET-FOREIGN-INVOICE";
    render({model}); openAccount(); tab("invoices"); expect(host.textContent).not.toContain("SECRET-FOREIGN-INVOICE"); expect(host.textContent).toContain("لم يكتمل التحقق من نطاقها");
});
test("view-only user cannot read invoice, daily or debt data", () => {
    render({permissions: [PREFIX + "view"]}); openAccount(); expect(host.querySelector('[data-tab="invoices"]').disabled).toBe(true);
    expect(host.querySelector('[data-tab="daily"]').disabled).toBe(true); expect(host.textContent).not.toContain("999,999.99"); expect(host.textContent).not.toContain("SYN-INVOICE-1");
});
test("duplicate identities block account display", () => {const model = fixture(); model.accounts.push({...model.accounts[0], linked_account_ref: "another-link"}); render({model}); expect(host.textContent).toContain("DUPLICATE_SOURCE"); expect(host.textContent).not.toContain("حساب اختباري فقط");});
test("search cannot create an unlinked account", () => {
    render(); const input = host.querySelector("input"); act(() => {Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "NEW-FREE-ID"); input.dispatchEvent(new Event("input", {bubbles: true}));});
    expect(host.textContent).toContain("لا توجد حسابات مطابقة"); expect(host.textContent).not.toContain("حساب اختباري فقط");
});
test("RTL arrow navigation selects the next permitted tab", () => {render(); openAccount(); act(() => host.querySelector('[data-tab="overview"]').dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowLeft", bubbles: true}))); expect(host.querySelector('[data-tab="daily"]').getAttribute("aria-selected")).toBe("true");});
test("revoking a selected tab permission hides its data immediately", () => {render(); openAccount(); tab("daily"); render({permissions: [PREFIX + "view"]}); expect(host.querySelector('[data-tab="overview"]').getAttribute("aria-selected")).toBe("true"); expect(host.textContent).not.toContain("SYN-FX-SOURCE");});
test("missing timezone has no provider-specific fallback", () => {const model = fixture(); model.accounts[0].timezone_name = null; render({model}); expect(host.textContent).toContain("المنطقة الزمنية مفقودة"); expect(host.textContent).not.toContain("New_York");});
test("incomplete summary value of zero is unavailable", () => {const model = fixture(); model.summary.today = {status: "incomplete", value: "0.00"}; render({model}); expect(host.querySelector('[data-card="today"]').textContent).toContain("غير متاح"); expect(host.querySelector('[data-card="today"]').textContent).not.toContain("0.00");});
