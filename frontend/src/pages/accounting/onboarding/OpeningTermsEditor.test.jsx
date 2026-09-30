import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import OpeningTermsEditor, { validateTermsRows } from "./OpeningTermsEditor";

test("fee contracts require explicit amounts and percentages in range, never learn from balances", () => {
    const providers = [{ id: "salla", name: "سلة" }];
    const row = { provider: "salla", mdr_percent: "2", fixed_fee_per_order: "0", vat_on_fees_percent: "15", evidence_ref: "fixture" };
    expect(validateTermsRows("payment_fees", [row], [], providers)).toEqual([]);
    expect(validateTermsRows("payment_fees", [{ ...row, mdr_percent: "101" }], [], providers).join(" ")).toContain("100%");
    expect(validateTermsRows("payment_fees", [{ ...row, fixed_fee_per_order: "" }], [], providers).join(" ")).toContain("صراحة");
    expect(validateTermsRows("payment_fees", [{ ...row, fixed_fee_per_order: "-1" }], [], providers).length).toBeGreaterThan(0);
});

test("prepaid/accrued UI validates only classifications supplied by the core", () => {
    const row = { classification: "fixture-classification", name: "اشتراك", entity_id: "fixture-entity", amount: "0", evidence_ref: "fixture" };
    expect(validateTermsRows("prepaid", [row], [{ id: "fixture-classification" }])).toEqual([]);
    expect(validateTermsRows("prepaid", [row], []).join(" ")).toContain("تصنيف النواة");
    expect(validateTermsRows("obligations", [{ ...row, evidence_ref: "" }], [{ id: "fixture-classification" }]).join(" ")).toContain("دليل");
});

test("changing fee provider clears previous provider's terms and evidence", () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div"), root = createRoot(container); document.body.appendChild(container);
    function Harness() {
        const [rows, setRows] = useState([{ provider: "a", mdr_percent: "2", fixed_fee_per_order: "3", vat_on_fees_percent: "15", evidence_ref: "A-proof" }]);
        return <OpeningTermsEditor domain="payment_fees" value={rows} onChange={setRows} providers={[{ id: "a", name: "A" }, { id: "b", name: "B" }]} feeConfigurationSupported />;
    }
    try {
        act(() => root.render(<Harness />));
        const select = container.querySelector("select");
        act(() => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, "b"); select.dispatchEvent(new Event("change", { bubbles: true })); });
        expect([...container.querySelectorAll("input")].every(input => input.value === "")).toBe(true);
    } finally { act(() => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; }
});
