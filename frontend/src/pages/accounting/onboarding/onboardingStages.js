// UX authority: Issue #1006, comment 5899581442. UI section IDs are not ledger categories.
export const ONBOARDING_STAGES = [
    ["cutover", "تاريخ القطع وبداية الدفتر"],
    ["banks", "البنوك والصناديق والحسابات المالية"],
    ["providers", "منصات الدفع وأرصدتها"],
    ["employees", "الموظفون: رواتب وسلف وعهد"],
    ["suppliers", "الموردون وأرصدتهم الافتتاحية"],
    ["external_persons", "الديون الخارجية: أموال لنا عند الناس"],
    ["courier_contracts", "شركات الشحن: العقود وشرائح COD"],
    ["courier_balances", "شركات الشحن: الأرصدة الافتتاحية"],
    ["drivers", "موصلو المتجر"],
    ["inventory", "المخزون والتكلفة ومواقع التخزين"],
    ["payment_fees", "عمولات طرق الدفع والضرائب"],
    ["advertising", "الحسابات والمحافظ الإعلانية"],
    ["prepaid", "المصروفات المدفوعة مقدمًا"],
    ["obligations", "المستحقات والودائع والالتزامات الأخرى"],
    ["review", "المراجعة النهائية والأدلة"],
    ["approval", "الاعتماد النهائي وفتح المحاسبة"],
].map(([id, label]) => ({ id, label }));

export const SECTION_LABELS = { incomplete: "ناقص", complete: "مكتمل", not_applicable: "لا ينطبق" };

const AMOUNT_FIELDS = new Set(["balance", "salary_payable", "advance", "custody", "payable", "receivable", "cod_receivable", "fee_payable", "prepaid_wallet", "amount", "opening_quantity", "opening_cod_receivable", "opening_payable"]);

export function sectionPresentation(section = {}, stageId = "") {
    const evidenceMissing = section.evidence_missing === true || !section.evidence_ref?.trim();
    const amounts = (section.rows || []).flatMap(row => Object.entries(row).filter(([key]) => AMOUNT_FIELDS.has(key)).map(([, value]) => value));
    const zeroConflict = section.explicit_zero === true && amounts.some(v => !/^0+(\.0+)?$/.test(String(v)));
    const naIncomplete = section.status === "not_applicable" && (stageId === "cutover" || !section.not_applicable_reason?.trim() || evidenceMissing || (section.rows || []).length > 0);
    const status = evidenceMissing || zeroConflict || naIncomplete || section.reconciliation_problem ? "incomplete" : section.status || "incomplete";
    return { status, evidenceMissing, zeroConflict, naIncomplete, explicitZero: section.explicit_zero === true && !zeroConflict && !evidenceMissing };
}

export function sectionIndicators(section = {}, stageId = "") {
    const view = sectionPresentation(section, stageId);
    const indicators = [SECTION_LABELS[view.status] || SECTION_LABELS.incomplete];
    if (view.explicitZero) indicators.push("صفر صريح");
    if (view.zeroConflict) indicators.push("تعارض مع إثبات الصفر");
    if (view.naIncomplete) indicators.push("عدم الانطباق يحتاج سببًا ودليلًا وقسمًا بلا بنود");
    if (view.evidenceMissing) indicators.push("دليل ناقص");
    if (section.reconciliation_problem === true) indicators.push("مشكلة مطابقة");
    return indicators;
}
