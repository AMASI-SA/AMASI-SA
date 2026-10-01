import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import OpeningEntityEditor, { DOMAIN_FIELDS, validateEntityRows } from "./OpeningEntityEditor";

const entities = [{ id: "entity-1", name: "طرف موجود" }, { id: "entity-2", name: "طرف آخر" }];
const banks = [{ id: "bank-1", name: "البنك الموثوق" }];
let container, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const field = label => container.querySelector(`[aria-label="${label}"]`);
const button = text => [...container.querySelectorAll("button")].find(b => b.textContent === text);
function change(label, value) {
    const target = field(label);
    act(() => {
        Object.getOwnPropertyDescriptor(target.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(target, value);
        target.dispatchEvent(new Event(target.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}
function Harness({ domain, createExternalPerson, initial = [] }) {
    const [value, setValue] = useState(initial);
    const [available, setAvailable] = useState(entities);
    return <OpeningEntityEditor domain={domain} value={value} onChange={setValue} entities={available} banks={banks} createExternalPerson={createExternalPerson} onEntityCreated={entity => setAvailable(current => [...current, entity])} />;
}

test("provider discovery selects only identity and never infers balance or settlement bank", () => {
    act(() => root.render(<Harness domain="providers" />));
    act(() => button("اختيار جهة موجودة").click());
    change("الجهة 1", "entity-1");
    expect(field("الرصيد المستحق لنا 1").value).toBe("");
    expect(field("بنك التسوية 1").value).toBe("");
    expect(field("الدليل المطلوب 1").value).toBe("");
    act(() => button("إثبات صفر — الرصيد المستحق لنا").click());
    expect(field("الرصيد المستحق لنا 1").value).toBe("0");
    expect(field("بنك التسوية 1").value).toBe("");
    change("بنك التسوية 1", "bank-1");
    expect(field("بنك التسوية 1").value).toBe("bank-1");
    expect(container.querySelector("section").dir).toBe("rtl");
    expect([...container.querySelectorAll("button")].every(b => b.type === "button" && !/Post|تفعيل|ترحيل|اعتماد/i.test(b.textContent))).toBe(true);
});

test("switching provider identity clears the previous balance bank and evidence", () => {
    act(() => root.render(<Harness domain="providers" initial={[{ entity_id: "entity-1", balance: "125", settlement_bank_id: "bank-1", evidence_ref: "old-evidence" }]} />));
    change("الجهة 1", "entity-2");
    expect(field("الجهة 1").value).toBe("entity-2");
    expect(field("الرصيد المستحق لنا 1").value).toBe("");
    expect(field("بنك التسوية 1").value).toBe("");
    expect(field("الدليل المطلوب 1").value).toBe("");
});

test("pending person creation disables list mutations so async response cannot drop newly added rows", async () => {
    let resolve;
    const createExternalPerson = jest.fn(() => new Promise(done => { resolve = done; }));
    act(() => root.render(<Harness domain="external_persons" initial={[{ entity_id: "entity-1", receivable: "0", evidence_ref: "fixture" }]} createExternalPerson={createExternalPerson} />));
    act(() => button("إضافة طرف جديد").click());
    change("اسم الطرف", "طرف جديد");
    change("هاتف الطرف", "0500000000");
    await act(async () => button("حفظ الطرف واختياره").click());
    expect(button("اختيار جهة موجودة").disabled).toBe(true);
    expect(button("إضافة طرف جديد").disabled).toBe(true);
    act(() => button("اختيار جهة موجودة").click());
    expect(field("الجهة 2")).toBeNull();
    await act(async () => resolve({ id: "created-id", name: "طرف جديد" }));
    expect(field("الجهة 1").value).toBe("entity-1");
    expect(field("الجهة 2").value).toBe("created-id");
    expect(button("اختيار جهة موجودة").disabled).toBe(false);
});

test("validator distinguishes missing amounts from explicit zero and requires evidence/real identity", () => {
    const row = { entity_id: "entity-1", balance: "0", evidence_ref: "fixture-evidence", settlement_bank_id: "bank-1" };
    expect(validateEntityRows([row], DOMAIN_FIELDS.providers, entities, banks, true)).toEqual([]);
    expect(validateEntityRows([{ ...row, balance: "" }], DOMAIN_FIELDS.providers, entities, banks, true).join(" ")).toContain("صراحة");
    expect(validateEntityRows([{ ...row, settlement_bank_id: "" }], DOMAIN_FIELDS.providers, entities, banks, true).join(" ")).toContain("بنك");
    expect(validateEntityRows([{ ...row, evidence_ref: "" }], DOMAIN_FIELDS.providers, entities, banks, true).join(" ")).toContain("دليل");
    expect(validateEntityRows([{ ...row, entity_id: "invented" }], DOMAIN_FIELDS.providers, entities).join(" ")).toContain("هوية");
    expect(validateEntityRows([row, row], DOMAIN_FIELDS.providers, entities).join(" ")).toContain("تكرار");
});

test("external person create submits name phone notes and selects real returned ID in same screen", async () => {
    const createExternalPerson = jest.fn().mockResolvedValue({ id: "server-real-id", name: "طرف جديد", phone: "0500000000", notes: "fixture note" });
    act(() => root.render(<Harness domain="external_persons" createExternalPerson={createExternalPerson} />));
    act(() => button("اختيار جهة موجودة").click());
    change("الجهة 1", "entity-1");
    expect(field("الجهة 1").value).toBe("entity-1");
    act(() => button("إضافة طرف جديد").click());
    change("اسم الطرف", "طرف جديد");
    change("هاتف الطرف", "0500000000");
    change("ملاحظات الطرف", "fixture note");
    await act(async () => button("حفظ الطرف واختياره").click());
    expect(createExternalPerson).toHaveBeenCalledTimes(1);
    expect(createExternalPerson).toHaveBeenCalledWith({ name: "طرف جديد", phone: "0500000000", notes: "fixture note" });
    expect(field("الجهة 2").value).toBe("server-real-id");
    expect(field("الجهة 1").value).toBe("entity-1");
    expect(field("مستحق لنا على الطرف 2").value).toBe("");
    expect(field("الدليل المطلوب 2").value).toBe("");
});

test("external person needs phone and rejects successful responses without entity identity", async () => {
    const createExternalPerson = jest.fn().mockResolvedValue({ name: "طرف جديد" });
    act(() => root.render(<Harness domain="external_persons" createExternalPerson={createExternalPerson} />));
    act(() => button("إضافة طرف جديد").click());
    change("اسم الطرف", "طرف جديد");
    await act(async () => button("حفظ الطرف واختياره").click());
    expect(createExternalPerson).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]').textContent).toContain("الهاتف");
    change("هاتف الطرف", "0500000000");
    await act(async () => button("حفظ الطرف واختياره").click());
    expect(container.querySelector('[role="alert"]').textContent).toContain("تعذر إنشاء الطرف");
    expect(field("الجهة 1")).toBeNull();
});


test("ad account selectors require explicit V2 binding and never infer from external_ref", () => {
    const accounts = [
        { id: "wallet-1", name: "Wallet 1", account_type: "ad_prepaid_wallet", status: "active", external_ref: "entity-1" },
        { id: "wallet-2", name: "Wallet 2", account_type: "ad_prepaid_wallet", status: "active", external_ref: "entity-2" },
        { id: "payable-1", name: "Payable 1", account_type: "ad_payable", status: "active", external_ref: "entity-1" },
        { id: "payable-2", name: "Payable 2", account_type: "ad_payable", status: "active", external_ref: "entity-2" },
    ];
    const render = row => act(() => root.render(<OpeningEntityEditor domain="advertising" value={[row]} onChange={() => {}} entities={[{ ...entities[0], prepaid_wallet_account_id: "wallet-1", payable_account_id: "payable-1" }]} financialAccounts={accounts} />));
    render({ entity_id: "entity-1" });
    const options = label => [...field(label).options].map(option => option.value);
    expect(options("حساب المحفظة المالي 1")).toEqual(["", "wallet-1"]);
    expect(options("حساب الذمة المالي 1")).toEqual(["", "payable-1"]);
    render({ entity_id: "", prepaid_wallet_account_id: "wallet-2" });
    expect(options("حساب المحفظة المالي 1")).toEqual([""]);
    expect(field("حساب المحفظة المالي 1").value).toBe("");
});

test("known source currency is readonly, FX visible, and explicit binding initializes without balances", () => {
    const entity = { id: "v2", name: "Connected account", provider: "meta_ads", external_account_id: "act-1", currency: "USD", prepaid_wallet_account_id: "wallet", payable_account_id: "payable" };
    function Bound() { const [rows, setRows] = useState([{ entity_id: "v2" }]); return <OpeningEntityEditor domain="advertising" value={rows} onChange={setRows} entities={[entity]} financialAccounts={[{id:"wallet",account_type:"ad_prepaid_wallet",status:"active",currency:"USD"},{id:"payable",account_type:"ad_payable",status:"active",currency:"USD"}]} />; }
    act(() => root.render(<Bound />));
    expect(field("العملة 1").value).toBe("USD");
    expect(field("العملة 1").readOnly).toBe(true);
    expect(field("سعر التحويل للريال 1")).not.toBeNull();
    expect(field("حساب المحفظة المالي 1").value).toBe("wallet");
    expect(field("محفظة مدفوعة مقدمًا 1").value).toBe("");
    expect(container.textContent).toContain("meta_ads");
    expect(container.textContent).toContain("act-1");
});

test("unknown ad currency fails visibly and missing bindings explain empty pickers", () => {
    act(() => root.render(<OpeningEntityEditor domain="advertising" value={[{entity_id:"v2"}]} onChange={() => {}} entities={[{id:"v2",name:"Account"}]} />));
    expect(field("العملة 1").value).toBe("");
    expect(container.textContent).toContain("عملة الحساب الإعلاني مجهولة");
    expect(container.textContent).toContain("لم يُنشأ حساب المحفظة المالي");
    expect(container.textContent).toContain("لم يُنشأ حساب الذمة المالي");
    expect(container.querySelector('a[href*="financial-accounts"]')).not.toBeNull();
});

test("provider canonical binding autoselects while invalid existing reference stays explicit", () => {
    function Bound({entity}) { const [rows,setRows]=useState([{entity_id:entity.id}]); return <OpeningEntityEditor domain="providers" value={rows} onChange={setRows} entities={[entity]} banks={banks} />; }
    act(() => root.render(<Bound entity={{id:"tabby",name:"Tabby",binding_status:"valid",bank_account_id:"bank-1"}} />));
    expect(field("بنك التسوية 1").value).toBe("bank-1");
    act(() => root.render(<OpeningEntityEditor domain="providers" value={[{entity_id:"tamara",settlement_bank_id:"legacy-bank"}]} onChange={() => {}} entities={[{id:"tamara",name:"Tamara",binding_status:"noncanonical",bank_account_id:"legacy-bank"}]} banks={[]} />));
    expect(container.textContent).toContain("legacy-bank");
    expect(container.textContent).toContain("الربط الحالي محفوظ");
});

test("employee without salary contract remains editable with diagnostic", () => {
    act(() => root.render(<OpeningEntityEditor domain="employees" value={[{entity_id:"e"}]} onChange={() => {}} entities={[{id:"e",name:"Employee",salary_contract_status:"missing"}]} />));
    expect(container.textContent).toContain("لا يوجد عقد راتب");
    expect(field("راتب مستحق 1")).not.toBeNull();
    expect(field("سلفة الموظف 1")).not.toBeNull();
    expect(field("عهدة الموظف 1")).not.toBeNull();
});
