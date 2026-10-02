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


test("ad account selectors use confirmed binding IDs even with unrelated external_ref", () => {
    const accounts = [
        { id: "wallet-1", name: "Wallet 1", account_type: "ad_prepaid_wallet", status: "active", external_ref: "entity-1" },
        { id: "wallet-2", name: "Wallet 2", account_type: "ad_prepaid_wallet", status: "active", external_ref: "entity-2" },
        { id: "payable-1", name: "Payable 1", account_type: "ad_payable", status: "active", external_ref: "entity-1" },
        { id: "payable-2", name: "Payable 2", account_type: "ad_payable", status: "active", external_ref: "entity-2" },
    ];
    const bindings = entities.map((entity, index) => ({ ...entity, funding_mode: "hybrid", wallet_financial_account_id: `wallet-${index + 1}`, payable_financial_account_id: `payable-${index + 1}` }));
    const render = row => act(() => root.render(<OpeningEntityEditor domain="advertising" value={[row]} onChange={() => {}} entities={bindings} financialAccounts={accounts.map(a => ({ ...a, external_ref: "unrelated" }))} />));
    render({ entity_id: "entity-1" });
    const options = label => [...field(label).options].map(option => option.value);
    expect(options("حساب المحفظة المالي 1")).toEqual(["", "wallet-1"]);
    expect(options("حساب الذمة المالي 1")).toEqual(["", "payable-1"]);
    render({ entity_id: "", prepaid_wallet_account_id: "wallet-2" });
    expect(options("حساب المحفظة المالي 1")).toEqual(["", "wallet-2"]);
    expect(field("حساب المحفظة المالي 1").value).toBe("wallet-2");
});

test.each(["prepaid", "postpaid", "hybrid"])("%s account discovery uses native mode without inferring amounts", mode => {
    const binding = { id: "binding", name: "حساب موثق", funding_mode: mode,
        wallet_financial_account_id: mode === "postpaid" ? null : "wallet",
        payable_financial_account_id: mode === "prepaid" ? null : "payable" };
    const accounts = [{ id: "wallet", name: "المحفظة", account_type: "ad_prepaid_wallet", currency: "USD", status: "active" },
        { id: "payable", name: "الذمة", account_type: "ad_payable", currency: "USD", status: "active" }];
    const Capture = () => { const [rows, setRows] = useState([]); return <OpeningEntityEditor domain="advertising" entities={[binding]} financialAccounts={accounts} value={rows} onChange={setRows} />; };
    act(() => root.render(<Capture />));
    act(() => button("إدخال الرصيد").click());
    expect(field("الجهة 1").value).toBe("binding");
    expect(Boolean(field("محفظة مدفوعة مقدمًا 1"))).toBe(mode !== "postpaid");
    expect(Boolean(field("مستحق للمنصة 1"))).toBe(mode !== "prepaid");
    if (mode !== "postpaid") { expect(field("محفظة مدفوعة مقدمًا 1").value).toBe(""); expect(field("حساب المحفظة المالي 1").value).toBe("wallet"); }
    if (mode !== "prepaid") { expect(field("مستحق للمنصة 1").value).toBe(""); expect(field("حساب الذمة المالي 1").value).toBe("payable"); }
});

test("bank table and search expose exact type currency and ID while preserving pending amount", () => {
    const account = { id: "canonical-bank", name: "البنك الكويتي", account_type: "bank", currency: "KWD", status: "active" };
    const Capture = () => { const [rows, setRows] = useState([]); return <OpeningEntityEditor domain="banks" entities={[account]} financialAccounts={[account]} value={rows} onChange={setRows} />; };
    act(() => root.render(<Capture />));
    expect(container.querySelector("table").textContent).toContain("canonical-bank");
    change("بحث في الجهات الموجودة", "KWD");
    act(() => button("إدخال الرصيد").click());
    expect(field("الرصيد الافتتاحي 1").value).toBe("");
    change("الرصيد الافتتاحي 1", "1.234");
    expect(field("الرصيد الافتتاحي 1").value).toBe("1.234");
    expect(field("الرصيد الافتتاحي 1").step).toBe("any");
    change("بحث في الجهات الموجودة", "لا توجد نتيجة");
    expect(field("الجهة 1").value).toBe(account.id);
    expect(field("الرصيد الافتتاحي 1").value).toBe("1.234");
});

test("empty catalog explains unresolved setup instead of supplying defaults", () => {
    act(() => root.render(<OpeningEntityEditor domain="banks" entities={[]} value={[]} onChange={() => {}} />));
    expect(container.textContent).toContain("القائمة الفارغة لا تعني أن الرصيد صفر");
    expect(container.querySelector("table")).toBeNull();
});
