import { act } from "react";
import { createRoot } from "react-dom/client";
import ProviderFeeExplanations, { ProviderFeeStatus, ProviderSourceAverage } from "./ProviderFeeDetails";

global.IS_REACT_ACT_ENVIRONMENT = true;

const source = (patch = {}) => ({
    available: true, profile_id: "source-sar", provider: "tabby", native_currency: "SAR", merchant_account: "account-a",
    rate_percent: "7.57473800", fixed_amount_per_sale: "1.00000000", vat_rate_percent: "15.00000000",
    vat_rate_status: "explicit_uniform", vat_rate: "0.15000000", calculation: "sale_variable_fees_before_vat_over_sale_principal",
    eligible_sale_rows: 10, principal_total: "1000.00", fee_variable_net_total: "75.74", fee_fixed_net_total: "10.00",
    statement_id: "statement-sar", statement_reference: "TABBY-SAR/2026-11-15", source_as_of: "2026-11-15",
    source_date_basis: "statement_date", date_precision: "day", accepted_at: "2026-11-15T20:00:00Z", ...patch,
});
const provider = (patch = {}) => ({
    id: "tabby", provider: "tabby", name: "تابي", fee_status: "estimated", fee_basis_sources: ["average_estimate"],
    fees: "355.43", actual_fees: "100.00", estimated_fees: "255.43", estimated_fees_complete: true,
    source_average_profiles: [source()], source_average_issues: [], average_fee_profiles: [], average_estimated_orders: 0,
    statement_evidence_as_of: "2026-11-15T20:00:00Z", pending_orders: 0, ...patch,
});

let host, root;
const render = async element => { await act(async () => root.render(element)); };
const facts = label => [...host.querySelectorAll("dt")].filter(item => item.textContent === label).map(item => item.nextElementSibling);
beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });

test("table summaries contain only the server status and one native-currency rate", async () => {
    const row = provider({ source_average_profiles: [source(), source({ profile_id: "source-kwd", native_currency: "KWD", rate_percent: "9.12500000" })] });
    await render(<><ProviderFeeStatus row={row} /><ProviderSourceAverage row={row} currency="SAR" /></>);
    expect(host.textContent).toBe("تقديرية7.574738%");
    expect(host.querySelector("bdi").getAttribute("dir")).toBe("ltr");
    expect(host.querySelector("button, dialog, details")).toBeNull();
    expect(host.textContent).not.toContain("KWD");
    expect(host.textContent).not.toContain("2026");
});

test.each([
    { label: "foreign only", profiles: [source({ native_currency: "KWD" })], issues: [] },
    { label: "multiple accounts", profiles: [source(), source({ profile_id: "second", merchant_account: "account-b" })], issues: [] },
    { label: "uncovered account", profiles: [source()], issues: [{ native_currency: "SAR", reason_code: "source_average_unavailable", scopes: 1 }] },
    { label: "malformed rate", profiles: [source({ rate_percent: "7.5" })], issues: [] },
])("$label never receives a guessed headline rate", async ({ profiles, issues }) => {
    await render(<ProviderSourceAverage row={provider({ source_average_profiles: profiles, source_average_issues: issues })} currency="SAR" />);
    expect(host.textContent).toBe("—");
    expect(host.querySelector('[aria-label="لا توجد نسبة موحدة لهذه العملة"]')).not.toBeNull();
});

test("one visible explanations section retains separated rate, fixed charge, VAT, reference and date", async () => {
    const row = provider();
    const snapshot = JSON.stringify(row);
    await render(<ProviderFeeExplanations rows={[row]} currency="SAR" notes={["شرح نطاق التقرير.", "شرح نطاق التقرير."]} />);
    expect(host.querySelector("h3").textContent).toBe("توضيحات");
    expect(host.querySelectorAll(".ofp-provider-table-notes p")).toHaveLength(1);
    expect(facts("العمولة المتغيرة قبل الضريبة")[0].textContent).toContain("7.574738%");
    expect(facts("متوسط الرسم الثابت لكل عملية")[0].textContent).toContain("1.00 SAR");
    expect(facts("ضريبة المصدر")[0].textContent).toBe("15%");
    expect(facts("مرجع الكشف")[0].querySelector("bdi").getAttribute("dir")).toBe("auto");
    expect(facts("مرجع الكشف")[0].textContent).toBe("TABBY-SAR/2026-11-15");
    expect(facts("تاريخ الكشف")[0].textContent).toBe("2026-11-15");
    expect(host.textContent).toContain("مصدر محفوظ؛ لم يُستخدم لتقدير رسوم في هذه اللقطة.");
    expect(host.querySelector("button, dialog, details")).toBeNull();
    expect(JSON.stringify(row)).toBe(snapshot);
});

test("foreign applied sources retain their native fixed charge and are explained below the report", async () => {
    const foreign = source({ profile_id: "source-kwd", native_currency: "KWD", fixed_amount_per_sale: "0.25050000", rate_percent: "9.12500000" });
    const row = provider({ source_average_profiles: [source(), foreign], average_fee_profiles: [foreign], average_estimated_orders: 1,
        average_variable_fees: "20.50", average_fixed_fees: "3.25", average_vat_fees: null,
        average_fixed_bases: ["statement_sale_mean"], average_operations_bases: ["one_provider_order_estimate"] });
    await render(<ProviderFeeExplanations rows={[row]} currency="SAR" notes={[]} />);
    const foreignBlock = host.querySelector(".ofp-provider-other-currencies");
    expect(foreignBlock.textContent).toContain("0.2505 KWD");
    expect(foreignBlock.textContent).not.toContain("0.2505 SAR");
    expect(foreignBlock.textContent).toContain("استُخدم في تقدير رسوم بعض الطلبات");
    expect(foreignBlock.querySelectorAll(".ofp-provider-source-card")).toHaveLength(1);
    expect(facts("العمولة المتغيرة التقديرية")[0].textContent).toContain("20.50 SAR");
    expect(facts("الضريبة التقديرية")[0].textContent).toBe("غير متاح");
    expect(host.textContent).toContain("افتُرض رسم ثابت واحد لبعض الطلبات");
});

test.each(["missing", "source_conflict", "mixed"])("%s VAT is not converted to zero or a confirmed percentage", async status => {
    await render(<ProviderFeeExplanations rows={[provider({ source_average_profiles: [source({ vat_rate_status: status, vat_rate_percent: null })] })]} currency="SAR" />);
    const tax = facts("ضريبة المصدر")[0].textContent;
    expect(tax).not.toContain("0%");
    expect(tax).not.toContain("15%");
    expect(tax).toMatch(/غير موثقة|تعارض|متعددة/);
});

test("partial-estimate explanations use their own counts and human reasons, not all pending estimates", async () => {
    const row = provider({ estimated_fees_complete: false, estimated_fee_incomplete_orders: 2,
        estimated_fee_calculated_orders: 283, pending_orders: 283, estimated_fee_unidentified_orders: 1,
        estimated_fee_scope_incomplete: true, estimated_fee_incomplete_reasons: [
            { code: "provider_fee_vat_rate_missing", orders: 1 }, { code: "unknown-private-diagnostic", orders: 1 },
        ] });
    await render(<ProviderFeeExplanations rows={[row]} currency="SAR" />);
    expect(host.textContent).toContain("طلبات لم تكتمل رسومها التقديرية: 2");
    expect(host.textContent).not.toContain("طلبات لم تكتمل رسومها التقديرية: 283");
    expect(host.textContent).toContain("نسبة ضريبة العمولة غير موثقة.");
    expect(host.textContent).toContain("تحتاج مطابقة دليل الرسوم.");
    expect(host.textContent).not.toContain("unknown-private-diagnostic");
    expect(facts("العمولات التقديرية المعلومة")[0].textContent).toContain("255.43 SAR · جزئي");
});

test("legacy explanations remain below the table without reparsing a sentence into a rate", async () => {
    const row = provider({ fee_status: undefined, source_average_profiles: undefined, source_average_issues: undefined,
        source_average_description: "شرح المصدر القديم بكامل مرجعه", fee_description: "شرح حالة الرسوم القديم" });
    await render(<><ProviderSourceAverage row={row} currency="SAR" /><ProviderFeeExplanations rows={[row]} currency="SAR" /></>);
    expect(host.firstElementChild.textContent).toBe("—");
    const notes = host.querySelector('[aria-label="توضيحات منصات الدفع"]');
    expect(notes.textContent).toContain("شرح المصدر القديم بكامل مرجعه");
    expect(notes.textContent).toContain("شرح حالة الرسوم القديم");
});
