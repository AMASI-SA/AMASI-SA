import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OpeningPrepaidEditor from "./OpeningPrepaidEditor";
import { calculatePrepaid, validatePrepaidRow } from "./prepaidCalculation";

const row = { source_mode: "obligation", obligation_id: "salla-v2", title: "Salla", coverage_start: "2026-08-21", coverage_end: "2027-08-21", payment_date: "2026-08-20", amount_paid: "3650.00", currency: "SAR", evidence_ref: "payment-proof" };
const obligations = [{ id: "salla-v2", title: "Salla", entity: "Salla", cycle: "annual", period_amount: "3650.00", coverage_start: row.coverage_start, coverage_end: row.coverage_end }];
test("annual period uses 41 consumed and 324 remaining actual days", () => {
    expect(calculatePrepaid(row, "2026-10-01")).toEqual({ coverage_days: 365, consumed_days: 41, remaining_days: 324, consumed_before_cutover: "410.00", prepaid_remaining_at_cutover: "3240.00" });
    expect(validatePrepaidRow(row, "2026-10-01", obligations)).toEqual([]);
});
test.each([{ amount_paid: "0" }, { amount_paid: "-1" }, { amount_paid: "1.001" }, { coverage_start: "2026-02-30" }, { payment_date: "2026-10-01" }, { payment_status: "unpaid" }, { currency: "USD" }])("rejects invalid prepaid %j", change => {
    expect(() => calculatePrepaid({ ...row, ...change }, "2026-10-01")).toThrow();
});
test("rejects missing V2 identity and authoritative unpaid status", () => {
    expect(validatePrepaidRow(row, "2026-10-01", [])).toContain("اختر التزامًا موجودًا في ميزان 2");
    expect(validatePrepaidRow(row, "2026-10-01", [{ ...obligations[0], payment_status: "unpaid" }])).toContain("المصدر يثبت أن الالتزام غير مدفوع");
});
test("renders source metadata and readonly allocation without any obligation writer", () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div"), root = createRoot(container);
    act(() => root.render(<OpeningPrepaidEditor value={[row]} obligations={obligations} cutoverDate="2026-10-01" onChange={() => {}} />));
    expect([...container.querySelectorAll("output")].map(node => node.textContent)).toEqual(["410.00", "3240.00"]);
    expect(container.textContent).toContain("مبلغ الفترة — ليس إثبات دفع");
    expect(container.querySelectorAll("input")).toHaveLength(5);
    act(() => root.unmount());
    delete global.IS_REACT_ACT_ENVIRONMENT;
});
