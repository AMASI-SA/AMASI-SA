import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { SupplierFinancialDetail, SupplierPaymentForm } from "./SupplierPaymentWorkspace";
import { recordMezanSupplierPayment, allocateMezanSupplierPayment } from "../../services/mezanSuppliersV2";

jest.mock("../../services/mezanSuppliersV2", () => ({ recordMezanSupplierPayment: jest.fn(), allocateMezanSupplierPayment: jest.fn() }));

const invoice = { id: "invoice-v2", supplier_id: "supplier-v2", invoice_number: "MZ2-1", total_halalas: 100000, paid_halalas: 25000, outstanding_halalas: 75000, financial_eligible: true, payment_status: "partial" };
const workspace = { financial_status: "available", payment_available: true, payment_accounts: [{ id: "canonical-bank", name: "بنك ميزان 2" }], payments: [{ id: "payment-v2", supplier_id: "supplier-v2", unallocated_payable_halalas: 10000, advance_available_halalas: 20000 }] };
let root;
let container;

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(global, "crypto", { configurable: true, value: { randomUUID: jest.fn(() => "7a31ca85-5f91-4d39-81f1-3e975c2304a1") } });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

async function mount(props = {}) {
    await act(async () => root.render(<SupplierPaymentForm supplierId="supplier-v2" workspace={workspace} invoices={[invoice]} onSaved={jest.fn()} onClose={jest.fn()} {...props} />));
    await field("المرجع", "REF-001");
}
async function field(label, value) {
    const node = [...container.querySelectorAll("label")].find((row) => row.textContent.startsWith(label)).querySelector("input,select,textarea");
    const prototype = node.tagName === "SELECT" ? HTMLSelectElement.prototype : node.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const property = node.type === "checkbox" ? "checked" : "value";
    await act(async () => {
        Object.getOwnPropertyDescriptor(prototype, property).set.call(node, value);
        node.dispatchEvent(new Event(node.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}
async function submit() { await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }

test("unready financial position displays unknown, never fabricated zero", () => {
    const html = renderToStaticMarkup(<SupplierFinancialDetail supplier={{ id: "supplier-v2", company_name: "مورد", financial: { outstanding_halalas: 0, advance_halalas: 0 } }} workspace={{ financial_status: "not_ready" }} />);
    expect(html).toContain("غير متاح");
    expect(html).not.toContain("0.00");
});

test("financial state, balances and historical invoice exclusion are visible", () => {
    const html = renderToStaticMarkup(<SupplierFinancialDetail supplier={{ id: "supplier-v2", company_name: "مورد", financial: { outstanding_halalas: 75000, advance_halalas: 20000 } }} invoices={[invoice, { ...invoice, id: "legacy", invoice_number: "OLD-1", financial_eligible: false }]} workspace={workspace} />);
    expect(html).toContain("750.00");
    expect(html).toContain("200.00");
    expect(html).toContain("مسددة جزئيًا");
    expect(html).toContain("لا ينشئ رصيدًا مستحقًا");
    expect((html.match(/>تسجيل سداد</g) || []).length).toBe(1);
});

test("canonical bank seam blocks payment submit without using legacy accounts", async () => {
    await mount({ workspace: { ...workspace, payment_available: false, payment_accounts: [] } });
    expect(container.querySelector('button[type="submit"]').disabled).toBe(true);
    expect(container.textContent).toContain("الحسابات المالية غير جاهزة");
    expect(container.textContent).not.toContain("Track A");
    await submit();
    expect(recordMezanSupplierPayment).not.toHaveBeenCalled();
});

test("payment submits V2 identities and immutable operation id across ambiguous retry", async () => {
    recordMezanSupplierPayment.mockRejectedValueOnce(new Error("network timeout")).mockResolvedValueOnce({});
    await mount({ initialInvoiceId: "invoice-v2" });
    await field("المبلغ", "250.00");
    await field("بنك / صندوق", "canonical-bank");
    await submit();
    expect(recordMezanSupplierPayment).toHaveBeenCalledWith("supplier-v2", expect.objectContaining({ invoice_id: "invoice-v2", financial_account_id: "canonical-bank", amount: "250.00", allow_advance: false }));
    expect(container.querySelector("fieldset").disabled).toBe(true);
    await submit();
    expect(recordMezanSupplierPayment).toHaveBeenCalledTimes(2);
    expect(recordMezanSupplierPayment.mock.calls[0]).toEqual(recordMezanSupplierPayment.mock.calls[1]);
});

test("allocation works without bank contract and excludes history from picker", async () => {
    allocateMezanSupplierPayment.mockResolvedValueOnce({});
    await mount({ mode: "allocation", workspace: { ...workspace, payment_available: false, payment_accounts: [] }, invoices: [invoice, { ...invoice, id: "legacy", invoice_number: "OLD", financial_eligible: false }] });
    expect(container.textContent).not.toContain("بنك / صندوق");
    expect(container.textContent).not.toContain("OLD");
    await field("المبلغ", "100.00");
    await field("الدفعة", "payment-v2");
    await field("الفاتورة", "invoice-v2");
    await submit();
    expect(allocateMezanSupplierPayment).toHaveBeenCalledWith("supplier-v2", expect.objectContaining({ payment_id: "payment-v2", invoice_id: "invoice-v2", amount: "100.00" }));
    expect(allocateMezanSupplierPayment.mock.calls[0][1]).not.toHaveProperty("financial_account_id");
});

test("successful write is not retried when workspace refresh fails", async () => {
    recordMezanSupplierPayment.mockResolvedValueOnce({});
    await mount({ onSaved: jest.fn().mockRejectedValue(new Error("refresh failed")) });
    await field("المبلغ", "100.00");
    await field("بنك / صندوق", "canonical-bank");
    await submit();
    expect(container.textContent).toContain("تم تسجيل العملية");
    expect(container.querySelector('button[type="submit"]').disabled).toBe(true);
    await submit();
    expect(recordMezanSupplierPayment).toHaveBeenCalledTimes(1);
});


test("explicit unallocated advance and overpayment consent are sent separately", async () => {
    recordMezanSupplierPayment.mockResolvedValueOnce({});
    await mount();
    await field("المبلغ", "1200.00");
    await field("بنك / صندوق", "canonical-bank");
    await field("نوع المبلغ", "advance");
    await act(async () => container.querySelector('input[type="checkbox"]').click());
    await submit();
    expect(recordMezanSupplierPayment).toHaveBeenCalledWith("supplier-v2", expect.objectContaining({ invoice_id: null, unallocated_kind: "advance", allow_advance: true, amount: "1200.00" }));
});

test.each([["paid", 100000, 0, "مسددة"], ["unpaid", 0, 100000, "غير مسددة"]])("invoice %s state shows server derived remaining", (payment_status, paid_halalas, outstanding_halalas, label) => {
    const html = renderToStaticMarkup(<SupplierFinancialDetail supplier={{ id: "supplier-v2", company_name: "مورد" }} invoices={[{ ...invoice, payment_status, paid_halalas, outstanding_halalas }]} workspace={workspace} />);
    expect(html).toContain(label);
    expect(html).toContain(outstanding_halalas === 0 ? "0.00" : "1,000.00");
    if (payment_status === "paid") expect(html).toMatch(/disabled=""[^>]*>تسجيل سداد/);
});


test("reference is required and notes are always a string", async () => {
    recordMezanSupplierPayment.mockResolvedValueOnce({});
    await mount();
    await field("المبلغ", "100.00");
    await field("بنك / صندوق", "canonical-bank");
    await field("المرجع", "  ");
    await submit();
    expect(recordMezanSupplierPayment).not.toHaveBeenCalled();
    expect(container.textContent).toContain("مرجع السداد مطلوب");
    await field("المرجع", "PAY-001");
    await submit();
    expect(recordMezanSupplierPayment.mock.calls[0][1]).toMatchObject({ reference: "PAY-001", notes: "" });
});

test("definitive 422 rejection unlocks fields for a corrected intent", async () => {
    recordMezanSupplierPayment.mockRejectedValueOnce(Object.assign(new Error("invalid date"), { status: 422 })).mockResolvedValueOnce({});
    await mount();
    await field("المبلغ", "100.00");
    await field("بنك / صندوق", "canonical-bank");
    await submit();
    expect(container.querySelector("fieldset").disabled).toBe(false);
    global.crypto.randomUUID.mockReturnValueOnce("corrected-intent");
    await field("المبلغ", "200.00");
    await submit();
    expect(recordMezanSupplierPayment.mock.calls[1][1]).toMatchObject({ operation_id: "corrected-intent", amount: "200.00" });
});


test("changing supplier clears an open payment action and its prior intent", async () => {
    const props = { invoices: [invoice], workspace, onClose: jest.fn(), onSaved: jest.fn() };
    await act(async () => root.render(<SupplierFinancialDetail {...props} supplier={{ id: "supplier-v2", company_name: "الأول" }} />));
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "دفع مبلغ للمورد").click());
    await field("المبلغ", "100.00");
    expect(container.querySelector("form")).not.toBeNull();
    await act(async () => root.render(<SupplierFinancialDetail {...props} supplier={{ id: "another-supplier-v2", company_name: "الثاني" }} />));
    expect(container.querySelector("form")).toBeNull();
    expect(recordMezanSupplierPayment).not.toHaveBeenCalled();
});
