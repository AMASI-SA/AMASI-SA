const BASES = {
    canonical_sales_status_scope_created_in_period_less_explicit_vat: "مبيعات الطلبات المنشأة في الفترة وفق حالات البيع المعتمدة، بعد الضريبة المثبتة",
    approved_salary_contract_calendar_accrual: "أيام الاستحقاق حسب عقود الرواتب وتواريخ سريانها",
    accepted_supplier_invoice_net_cost_with_complete_physical_components: "صافي تكلفة فواتير المورد المقبولة بمكونات القطع المثبتة",
    recorded_shipping_event_gross_cost: "تكلفة أحداث الشحن المسجلة، شاملة الضريبة قبل تحديد معالجتها",
    saved_effective_tariff_including_fee_vat: "التعرفة المحفوظة السارية، شاملة ضريبة الرسوم قبل تحديد معالجتها",
    canonical_recurring_calendar_accrual_SAR: "استحقاق المصروفات الدورية بالريال حسب الفترة",
    canonical_recurring_calendar_accrual_SAR_before_input_tax_review: "استحقاق المصروفات الدورية بالريال قبل مراجعة ضريبة المدخلات",
    canonical_expense_accrual_SAR_before_input_tax_review: "مصروفات الفترة بالريال قبل مراجعة ضريبة المدخلات",
    verified_closed_daily_consumption_saved_SAR_conversion: "استهلاك إعلاني يومي مثبت بتحويل الريال المحفوظ",
};
const ISSUES = {
    profit_canonical_orders_missing: "مصدر الطلبات المعتمد غير متاح",
    profit_order_snapshot_unverified: "لقطة الطلب غير مثبتة ضمن وقت التقرير",
    profit_return_sales_tax_basis_required: "يلزم إثبات أثر المرتجع على صافي المبيعات والضريبة",
    profit_sale_amount_or_tax_missing: "مبلغ البيع أو ضريبته غير مثبت",
    profit_sale_currency_missing: "عملة البيع غير مثبتة",
    profit_sale_tax_exceeds_total: "ضريبة البيع تتجاوز الإجمالي وتحتاج تصحيح المصدر",
    profit_sales_policy_ambiguous: "سياسة نطاق المبيعات متعارضة",
    profit_sales_policy_status_unverified: "حالة الطلب غير مثبتة ضمن سياسة البيع",
    profit_employee_identity_ambiguous: "هوية الموظف متعارضة",
    profit_salary_revision_missing: "يلزم راتب معتمد ساري لأيام الاستحقاق المحددة",
    profit_salary_source_or_cutover_unverified: "مصدر الرواتب أو بداية الاستحقاق غير مثبت",
    profit_shipping_input_vat_treatment_required: "يلزم تحديد معالجة ضريبة مدخلات الشحن",
    profit_fee_input_vat_treatment_required: "يلزم تحديد معالجة ضريبة رسوم الدفع",
    profit_payment_fee_scope_missing: "نطاق عمليات رسوم الدفع غير مثبت",
    profit_payment_fee_tariff_or_fx_missing: "تعرفة الدفع السارية أو دليل تحويل العملة غير متاح",
    profit_shipping_recognition_evidence_missing: "يلزم دليل حدث احتساب تكلفة الشحن",
    profit_shipping_source_incomplete: "مصدر تكلفة الشحن غير مكتمل",
    profit_shipping_zero_requires_scope: "يلزم إثبات عدم انطباق تكلفة الشحن قبل اعتماد الصفر",
    profit_supplier_receipt_not_reconciled: "استلام المورد لم يطابق فاتورته",
    profit_bundle_cost_evidence_required: "يلزم دليل تكلفة مكونات الحزمة",
    profit_invoice_component_coverage_unverified: "تغطية مكونات القطعة بالفاتورة غير مثبتة",
    profit_invoice_component_source_invalid: "مصدر مكونات الفاتورة غير صالح",
    profit_product_cost_source_incomplete: "مصدر تكلفة المنتج غير مكتمل",
    profit_product_item_identity_ambiguous: "هوية بند المنتج متعارضة",
    profit_product_items_missing: "بنود المنتجات غير متاحة",
    profit_product_quantity_invalid: "كمية المنتج غير صالحة",
    profit_received_product_units_unverified: "القطع المستلمة غير مثبتة",
    profit_ad_daily_consumption_proof_required: "يلزم إثبات الاستهلاك الإعلاني لليوم المحدد",
    profit_ad_consumption_fx_required: "يلزم تحويل عملة مثبت للاستهلاك الإعلاني",
    profit_ad_applicability_unverified: "نطاق الحسابات الإعلانية المنطبقة غير مثبت",
    profit_ad_funding_authority_conflict: "مصادر تمويل الحساب الإعلاني متعارضة",
    profit_ad_binding_evidence_required: "يلزم دليل ربط الحساب الإعلاني",
    profit_ad_account_identity_ambiguous: "هوية الحساب الإعلاني متعارضة",
    profit_ad_binding_after_asof: "ربط الحساب الإعلاني لاحق للقطة التقرير",
    profit_ad_account_missing_or_ambiguous: "الحساب الإعلاني مفقود أو متعارض",
    profit_ad_account_contract_incomplete: "بيانات الحساب الإعلاني غير مكتملة",
    profit_ad_period_unbounded: "فترة الاستهلاك الإعلاني غير محددة",
    profit_expense_input_tax_basis_required: "يلزم تحديد معالجة ضريبة مدخلات المصروف",
    profit_utility_actual_invoice_missing_or_ambiguous: "فاتورة الخدمة الفعلية مفقودة أو متعارضة",
    profit_utility_invoice_after_asof: "فاتورة الخدمة لاحقة للقطة التقرير",
    profit_legacy_recurring_overlap_review_required: "يلزم مراجعة تداخل المصروف السابق مع المصروف الدوري",
    profit_nonemployee_salary_business_classification_required: "يلزم تصنيف المصروف المسجل كراتب لغير موظف",
    profit_prepaid_consumption_end_required: "يلزم تاريخ نهاية استهلاك المصروف المدفوع مقدمًا",
    profit_bank_fee_currency_or_identity_conflict: "عملة أو هوية رسم البنك متعارضة",
    profit_bank_fee_evidence_required: "يلزم دليل رسم البنك",
    profit_direct_expense_accrual_link_required: "يلزم ربط الحركة البنكية باستحقاق المصروف",
    profit_direct_expense_event_required: "يلزم حدث إثبات المصروف المباشر",
};
for (const prefix of ["expense", "recurring"]) {
    Object.entries({
        period_unbounded: "فترة المصروف غير محددة", period_invalid: "فترة المصروف غير صالحة",
        identity_or_currency_conflict: "هوية أو عملة المصروف متعارضة", cycle_missing: "دورة المصروف الدوري غير محددة",
        type_unverified: "نوع المصروف غير مثبت", historical_revision_required: "يلزم إصدار تاريخي معتمد للمصروف",
        historical_status_required: "يلزم إثبات حالة المصروف التاريخية", snapshot_after_asof: "لقطة المصروف لاحقة لوقت التقرير",
        contract_incomplete: "بيانات المصروف غير مكتملة", source_scope_unverified: "نطاق مصدر المصروف غير مثبت",
        source_scope_invalid: "نطاق مصدر المصروف غير صالح", source_rejected: "مصدر المصروف لم يجتز التحقق",
        movement_scope_too_large: "نطاق حركات المصروف يتجاوز حد التحقق ويلزم تضييقه",
    }).forEach(([key, label]) => { ISSUES[`profit_${prefix}_${key}`] = label; });
}

export const profitRowAmount = row => row.period_evidence && row.period_evidence.complete !== true ? null : row.amount;
export function profitEvidenceLines(evidence, money, count) {
    if (!evidence || typeof evidence !== "object") return [];
    const lines = [
        `المجموع المثبت المتاح: ${money(evidence.known_subtotal)}`,
        `تقدير تكلفة غير مثبتة: ${money(evidence.estimated_subtotal)}`,
        `عدد سجلات المصدر: ${count(evidence.source_count)}`,
        `أساس الحساب: ${BASES[evidence.basis] || "أساس الحساب يحتاج مراجعة المصدر"}`,
    ];
    if (evidence.complete !== true) lines.push("المجموع المتاح جزئي؛ لا يمثل إجمالي البند أو الربح المكتمل.");
    if (evidence.estimated_subtotal != null) lines.push("التقدير مستقل عن التكلفة المثبتة ولا يُجمع معها باعتباره تكلفة فعلية.");
    for (const issue of Array.isArray(evidence.issues) ? evidence.issues : []) {
        const detail = [issue.source_id && `مرجع المصدر: ${issue.source_id}`, issue.date && `التاريخ: ${issue.date}`].filter(Boolean).join(" · ");
        lines.push(`${ISSUES[issue.code] || "يلزم مراجعة دليل المصدر لهذا البند"}${detail ? ` — ${detail}` : ""}`);
    }
    return lines;
}
