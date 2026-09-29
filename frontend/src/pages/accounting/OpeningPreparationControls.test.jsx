import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OpeningIdentityInput from "./OpeningIdentityInput";
import OpeningProviderBindings from "./OpeningProviderBindings";
import { saveOpeningProviderBankBinding } from "../../services/accountingModule";

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("../../services/accountingModule", () => ({ saveOpeningProviderBankBinding: jest.fn() }));

let root, node;
beforeEach(() => {
    jest.resetAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div");
    document.body.appendChild(node);
    root = createRoot(node);
});
afterEach(() => { act(() => root.unmount()); node.remove(); });
async function render(element) { await act(async () => { root.render(element); }); }
async function choose(label, value) {
    const select = node.querySelector(`[aria-label="${label}"]`);
    await act(async () => {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
    });
}
async function click(element) { await act(async () => { element.click(); }); }

test("counterparty is selected by the server identity and cannot be invented", async () => {
    const onChange = jest.fn();
    await render(<OpeningIdentityInput line={{ category: "supplier_payable", entity_id: "" }} index={0}
        context={{ entities: { supplier_payable: [{ id: "supplier-exact", name: "المورد المسجل" }] } }} onChange={onChange} />);
    expect(node.querySelector("input")).toBeNull();
    expect([...node.querySelectorAll("option")].map((option) => option.value)).toEqual(["", "supplier-exact"]);
    await choose("هوية الطرف للسطر 1", "supplier-exact");
    expect(onChange).toHaveBeenLastCalledWith({ entity_id: "supplier-exact", label: "المورد المسجل" });
    await choose("هوية الطرف للسطر 1", "invented");
    expect(onChange).toHaveBeenLastCalledWith({ entity_id: "", label: "" });
});

test("missing identity context disables selection instead of offering free text", async () => {
    await render(<OpeningIdentityInput line={{ category: "employee_advance", entity_id: "" }} index={0}
        context={null} onChange={jest.fn()} />);
    expect(node.querySelector("select").disabled).toBe(true);
    expect(node.querySelector("input")).toBeNull();
    expect(node.textContent).toContain("تعذر التحقق");
});

const context = {
    entities: { provider_receivable: [{ id: "salla", name: "سلة" }] },
    banks: [{ id: "bank-exact", name: "البنك المعرف" }, { id: "bank-other", name: "البنك الآخر" }],
    provider_bindings: [{ provider: "salla", configured: false, needs_confirmation: true }],
};

test("provider binding needs bank, evidence and an explicit confirmation; mount never writes", async () => {
    const onSaved = jest.fn();
    saveOpeningProviderBankBinding.mockResolvedValue({ configured: true });
    await render(<OpeningProviderBindings context={context} canManage evidenceFileId="" onSaved={onSaved} />);
    expect(saveOpeningProviderBankBinding).not.toHaveBeenCalled();
    await choose("بنك salla", "bank-exact");
    await click(node.querySelector('[aria-label="تأكيد بنك salla"]'));
    expect(node.querySelector("button").disabled).toBe(true);
    await render(<OpeningProviderBindings context={context} canManage evidenceFileId="providers-proof" onSaved={onSaved} />);
    expect(saveOpeningProviderBankBinding).not.toHaveBeenCalled();
    expect(node.querySelector("button").disabled).toBe(false);
    await click(node.querySelector("button"));
    expect(saveOpeningProviderBankBinding).toHaveBeenCalledTimes(1);
    expect(saveOpeningProviderBankBinding).toHaveBeenCalledWith("salla", {
        bank_account_id: "bank-exact", evidence_ref: "providers-proof", confirmed: true,
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
    await choose("بنك salla", "bank-other");
    expect(node.querySelector("button").disabled).toBe(true);
    expect(node.querySelector('[aria-label="تأكيد بنك salla"]').checked).toBe(false);
});

test("view authority alone cannot change provider bank routing", async () => {
    await render(<OpeningProviderBindings context={context} canManage={false} evidenceFileId="proof" onSaved={jest.fn()} />);
    expect(node.querySelector("button")).toBeNull();
    expect(node.querySelector("select")).toBeNull();
    expect(saveOpeningProviderBankBinding).not.toHaveBeenCalled();
});
