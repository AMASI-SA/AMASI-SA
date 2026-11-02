import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import CurrencyFields, { CurrencySelect } from "./CurrencyFields";
import catalog from "./currencyCodes.json";
import { accountFx, fxTimeInRiyadh } from "./currencyRules";
import OpeningEntityEditor from "./onboarding/OpeningEntityEditor";
import OpeningTermsEditor from "./onboarding/OpeningTermsEditor";
import { buildFinancialSection } from "./onboarding/onboardingFinancialAdapter";

let node, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); });
afterEach(() => { act(() => root.unmount()); node.remove(); });
function change(label, value) {
    const element = node.querySelector(`[aria-label="${label}"]`);
    act(() => {
        Object.getOwnPropertyDescriptor(element.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(element, value);
        element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}

test("frontend and backend use the exact same pinned ISO list", () => {
    expect(catalog).toEqual(JSON.parse(fs.readFileSync(path.join(__dirname, "../../../../backend/accounting_currency_codes.json"), "utf8")));
    expect(catalog.codes).toEqual(expect.arrayContaining(["SAR", "USD", "EUR", "AED"]));
    expect(catalog.codes).not.toContain("ZZZ");
});

test("SAR is default; foreign selection reveals required FX, returning to SAR resets parity and stale evidence", () => {
    let latest;
    function Harness() { const [row, setRow] = useState({}); latest = row; return <CurrencyFields row={row} onChange={patch => setRow({ ...row, ...patch })} />; }
    act(() => root.render(<Harness />));
    expect(node.querySelector("select").value).toBe("SAR");
    expect(node.querySelector('input[type="number"]')).toBeNull();
    change("العملة", "USD");
    expect(node.querySelector('input[type="number"]').required).toBe(true);
    expect(node.querySelector('input[type="datetime-local"]').required).toBe(true);
    change("سعر التحويل العملة", "3.75");
    change("مصدر التحويل العملة", "independent-fx-source");
    change("العملة", "SAR");
    expect(latest).toMatchObject({ original_currency: "SAR", fx_rate_to_sar: "1", fx_source: "", fx_at: "", fx_evidence_file_id: "" });
    expect(node.querySelector("input")).toBeNull();
});

test("even an injected select option is rejected and unknown saved currency is not presented as SAR", () => {
    const onChange = jest.fn();
    act(() => root.render(<CurrencySelect value="ZZZ" onChange={onChange} />));
    const option = document.createElement("option"); option.value = "ZZZ";
    node.querySelector("select").appendChild(option);
    change("العملة", "ZZZ");
    expect(onChange).not.toHaveBeenCalled();
    expect(node.textContent).toContain("عملة غير متاحة");
});

test("MZ2 account currency is read-only and absent account currency cannot default to SAR", () => {
    act(() => root.render(<CurrencyFields row={{ original_currency: "SAR" }} accountBound account={{ currency: "USD" }} onChange={jest.fn()} />));
    expect(node.querySelector("select")).toBeNull();
    expect(node.querySelector("output").textContent).toBe("USD");
    expect(node.querySelector('input[type="number"]')).not.toBeNull();
    act(() => root.render(<CurrencyFields row={{ original_currency: "SAR" }} accountBound account={{}} onChange={jest.fn()} />));
    expect(node.querySelector("output").textContent).toBe("BLOCKED_BY_BACKEND / not_ready");
    expect(node.querySelector("select")).toBeNull();
});

test.each(["prepaid", "obligations"])("%s editor exposes controlled currency and FX", domain => {
    function Harness() { const [value, setValue] = useState([{}]); return <OpeningTermsEditor domain={domain} value={value} onChange={setValue} />; }
    act(() => root.render(<Harness />));
    change("عملة البند 1", "EUR");
    expect(node.querySelector('[aria-label="سعر التحويل عملة البند 1"]').required).toBe(true);
});

test("advertising accounts keep different currencies and FX snapshots separate in the existing opening-line contract", () => {
    const accounts = [{ id: "wallet", currency: "USD", account_type: "ad_prepaid_wallet", status: "active" }, { id: "debt", currency: "EUR", account_type: "ad_payable", status: "active" }];
    const entities = [
        { id: "usd-binding", name: "USD prepaid", funding_mode: "prepaid", currency: "USD", wallet_financial_account_id: "wallet" },
        { id: "eur-binding", name: "EUR postpaid", funding_mode: "postpaid", currency: "EUR", payable_financial_account_id: "debt" },
    ];
    let latest;
    function Harness() {
        const [rows, setRows] = useState([
            { entity_id: "usd-binding", prepaid_wallet: "20", prepaid_wallet_account_id: "wallet" },
            { entity_id: "eur-binding", payable: "30", payable_account_id: "debt" },
        ]); latest = rows;
        return <OpeningEntityEditor domain="advertising" value={rows} onChange={setRows} entities={entities} financialAccounts={accounts} />;
    }
    act(() => root.render(<Harness />));
    expect(node.querySelector('[aria-label="عملة المحفظة 1"]').textContent).toBe("USD");
    expect(node.querySelector('[aria-label="عملة الذمة 2"]').textContent).toBe("EUR");
    change("سعر التحويل عملة المحفظة 1", "3.75");
    change("سعر التحويل عملة الذمة 2", "4.10");
    const result = buildFinancialSection("advertising", { sections: { advertising: { rows: latest } } }, {}, { financial_accounts: accounts, entities: { ad_accounts: entities } });
    expect(result.data.lines.map(line => [line.financial_account_id, line.original_currency, line.original_amount, line.fx_rate_to_sar])).toEqual([["wallet", "USD", "20", "3.75"], ["debt", "EUR", "30", "4.10"]]);
    expect(result.data.lines.every(line => !Object.hasOwn(line, "account_fx"))).toBe(true);
});

test("switching account denomination does not reuse old FX and invalid codes fail adapter projection", () => {
    expect(accountFx({ original_currency: "USD", fx_rate_to_sar: "3.75", fx_source: "old" }, "bank", { currency: "EUR" })).toMatchObject({ original_currency: "EUR", fx_rate_to_sar: "", fx_source: "" });
    expect(() => buildFinancialSection("obligations", { sections: { obligations: { rows: [{ classification: "other_payable", entity_id: "x", amount: "1", original_currency: "ZZZ" }] } } })).toThrow("onboarding_currency_invalid");
});

test.each(["", null, "ZZZ"])("an explicitly invalid saved currency %s never defaults to SAR", currency => {
    act(() => root.render(<CurrencyFields row={{ original_currency: currency }} onChange={jest.fn()} />));
    expect(node.querySelector("select").value).toBe("");
    expect(() => buildFinancialSection("obligations", { sections: { obligations: { rows: [{ classification: "other_payable", entity_id: "x", amount: "1", original_currency: currency }] } } })).toThrow("onboarding_currency_invalid");
});

test("restored FX timestamp displays Riyadh time without shifting the recorded instant", () => {
    expect(fxTimeInRiyadh("2026-09-30T21:00:00Z")).toBe("2026-10-01T00:00");
    expect(fxTimeInRiyadh("2026-10-01T00:00:00+03:00")).toBe("2026-10-01T00:00");
    expect(fxTimeInRiyadh("2026-10-01T00:00")).toBe("2026-10-01T00:00");
    act(() => root.render(<CurrencyFields row={{ original_currency: "USD", fx_at: "2026-09-30T21:00:00Z" }} onChange={jest.fn()} />));
    expect(node.querySelector('input[type="datetime-local"]').value).toBe("2026-10-01T00:00");
});
