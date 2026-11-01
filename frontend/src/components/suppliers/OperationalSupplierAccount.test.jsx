import { renderToStaticMarkup } from "react-dom/server";
import OperationalSupplierDetail, { OperationalSupplierSummary, formatOperationalAmount } from "./OperationalSupplierAccount";

jest.mock("react-router-dom", () => ({ Link: ({ to, children }) => <a href={to}>{children}</a> }));

test("money formatting retains cents and never turns unknown into zero", () => {
    expect(formatOperationalAmount("12345.67")).toBe("12,345.67");
    expect(formatOperationalAmount("0.00")).toBe("0.00");
    expect(formatOperationalAmount(null)).toBe("غير متاح");
    expect(formatOperationalAmount("NaN")).toBe("غير متاح");
});

test("supplier detail isolates the selected supplier and requires movement permission", () => {
    const markup = renderToStaticMarkup(<OperationalSupplierDetail supplier={{ id: "one", company_name: "مورد تجريبي" }} workspace={{
        accounts: [{ supplier_id: "one", currency: "SAR", outstanding_payable: "40.00", outstanding_receivable: "0.00" }],
        invoices: [{ id: "foreign", supplier_id: "two", invoice_number: "DO-NOT-SHOW", currency: "SAR" }],
        openings: [], permissions: { record_movements: false },
    }} onClose={() => {}} />);
    expect(markup).toContain("40.00");
    expect(markup).not.toContain("DO-NOT-SHOW");
    expect(markup).not.toContain('href="/operational-balances"');
    expect(markup).not.toContain("<form");
});

test("supplier detail keeps evidence review warning visible above the invoice list", () => {
    const markup = renderToStaticMarkup(<OperationalSupplierDetail supplier={{ id: "one", company_name: "مورد تجريبي" }} workspace={{
        accounts: [{ supplier_id: "one", currency: "SAR", outstanding_payable: "40.00", outstanding_receivable: "0.00" }],
        invoices: [], openings: [], source_issues_count: 1,
        permissions: { record_movements: false },
    }} onClose={() => {}} />);
    expect(markup).toContain('role="status"');
    expect(markup).toContain("توجد بيانات فواتير تحتاج مراجعة");
    expect(markup).toContain("قبل اعتماد الحساب");
    expect(markup).toContain("40.00");
});

test("summary retains currency separation and exposes incomplete source evidence", () => {
    const markup = renderToStaticMarkup(<OperationalSupplierSummary workspace={{
        summaries: { SAR: { payable: "100.00", receivable: "0.00", settled: "20.00", invoice_count: 1 }, USD: { payable: "30.00", receivable: "0.00", settled: "0.00", invoice_count: 1 } },
        invoice_provenance_incomplete: true, source_issues_count: 1,
    }} />);
    expect(markup).toContain("100.00");
    expect(markup).toContain("30.00");
    expect(markup).not.toContain("130.00");
    expect(markup).toContain("SAR");
    expect(markup).toContain("USD");
    expect(markup).toContain("توجد بيانات تحتاج مراجعة");
    expect(markup).toContain("لم تكتمل قراءة الفواتير التشغيلية بعد");
});
