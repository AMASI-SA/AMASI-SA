// Synthetic only: reviewed Track G candidate GET responses; never a live backend.
export function fixture(url, params) {
    const base = "/accounting-module/onboarding";
    if (url === `${base}/definitions`) return { ssot_setup_version: 1, external_person_registry: "mz2_external_persons_v2" };
    if (url === `${base}/typed-facts`) return { items: [
        { id: "SYN-G-VAT-SALES", display_name: "ضريبة مبيعات موثقة", category: "sales_vat_payable", amount: "150.00", currency: "SAR", cutover_date: "2026-10-01", status: "active", evidence: "SYN-EVIDENCE-SALES" },
        { id: "SYN-G-VAT-INPUT", display_name: "ضريبة مدخلات موثقة", category: "input_vat", amount: "45.00", currency: "SAR", cutover_date: "2026-10-01", status: "active", evidence: "SYN-EVIDENCE-INPUT" },
        { id: "SYN-G-ACCRUAL", display_name: "مصروف مستحق تجريبي", category: "accrued_expense", amount: "0.00", currency: "SAR", cutover_date: "2026-10-01", status: "active", evidence: "SYN-EVIDENCE-ACCRUAL" },
    ] };
    if (url === `${base}/fee-policies`) return { items: [{ id: "SYN-G-FEE", provider: "salla", percentage: "1.50", fixed_amount: "1.00", minimum: null, maximum: null, currency: "SAR", vat_treatment: "exclusive", effective_from: "2026-10-01", effective_to: null, status: "active", evidence: "SYN-EVIDENCE-FEE" }] };
    if (url === `${base}/identities/external_person`) return { items: [{ id: "SYN-G-PERSON", label: "طرف تجريبي", kind: "external_person", version: 1 }] };
    if (url === `${base}/prepaid-candidates` && params?.cutover !== "2026-10-01") return { source: "operating_recurring_obligations_v2", blockers: [{ code: "synthetic_fixture_date_unavailable" }], items: [] };
    if (url === `${base}/prepaid-candidates`) return { source: "operating_recurring_obligations_v2", blockers: [], items: [{ invoice_id: "SYN-G-INVOICE", obligation_id: "SYN-G-OBLIGATION", title: "اشتراك سنوي تجريبي", coverage_start: "2026-08-21", coverage_end: "2027-08-21", payment_amount: "3660.00", payment_date: "2026-08-21", currency: "SAR", source_stale: false, selection: { id: "SYN-G-SELECTION" }, evidence: "SYN-EVIDENCE-PREPAID", calculation: { eligible: true, cutover_date: params?.cutover, consumed_before_cutover: "410.00", remaining_prepaid_after_cutover: "3250.00" } }] };
    return undefined;
}
