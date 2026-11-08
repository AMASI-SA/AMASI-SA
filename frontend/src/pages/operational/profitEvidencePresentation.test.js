import { profitEvidenceLines, profitRowAmount } from "./profitEvidencePresentation";
import { formatPositionMoney, formatPositionCount } from "./financialPositionPresentation";

test("unknown evidence never turns into zero or leaks raw codes", () => {
    const lines = profitEvidenceLines({ complete: false, known_subtotal: null, source_count: null,
        issues: [{ code: "unrecognized_internal_code", source_id: "source-1", date: "2026-11-01" }] }, formatPositionMoney, formatPositionCount).join("\n");
    expect(lines).toContain("المجموع المثبت المتاح: غير متاح");
    expect(lines).toContain("تقدير تكلفة غير مثبتة: غير متاح");
    expect(lines).not.toContain("0.00");
    expect(lines).not.toContain("unrecognized_internal_code");
    expect(lines).toContain("source-1");
    expect(lines).toContain("2026-11-01");
});

test("only complete evidence permits the supplied total; partial subtotals are never promoted", () => {
    expect(profitRowAmount({ amount: "50.00", period_evidence: { known_subtotal: "50.00" } })).toBeNull();
    expect(profitRowAmount({ amount: "50.00", period_evidence: { complete: true } })).toBe("50.00");
    expect(profitRowAmount({ amount: null, period_evidence: { complete: true, known_subtotal: "50.00" } })).toBeNull();
});

test.each([
    ["recorded_shipping_event_gross_cost", "profit_shipping_input_vat_treatment_required", "شاملة الضريبة", "ضريبة مدخلات الشحن"],
    ["saved_effective_tariff_including_fee_vat", "profit_fee_input_vat_treatment_required", "التعرفة المحفوظة السارية", "ضريبة رسوم الدفع"],
    ["verified_closed_daily_consumption_saved_SAR_conversion", "profit_ad_consumption_fx_required", "تحويل الريال المحفوظ", "تحويل عملة مثبت"],
])("basis %s keeps tax and FX uncertainty visible", (basis, code, basisText, issueText) => {
    const lines = profitEvidenceLines({ basis, complete: false, known_subtotal: "34.50", source_count: 1, issues: [{ code }] }, formatPositionMoney, formatPositionCount).join("\n");
    expect(lines).toContain(basisText); expect(lines).toContain(issueText);
});
