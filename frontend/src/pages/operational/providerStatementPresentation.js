export const STATEMENT_PROVIDERS = { tabby: "تابي", tamara: "تمارا", salla: "سلة" };
export const STATEMENT_KINDS = { capture: "دفعة عميل", refund: "رد مبلغ", payout_fee: "رسم تسوية", other: "عملية أخرى" };
export const STATEMENT_STATUSES = {
    matched: "مطابق للسجل", missing: "غير مسجل", different: "يحتاج مطابقة", blocked: "يحتاج معالجة", already_confirmed: "معتمد سابقًا",
};
export const STATEMENT_COUNTS = [
    ["matched", "مطابق"], ["missing", "غير مسجل"], ["different", "يحتاج مطابقة"], ["blocked", "يحتاج معالجة"], ["confirmed", "معتمد سابقًا"],
];
export const STATEMENT_RECORD_EFFECTS = {
    already_recorded: "مسجل ماليًا", evidence_only: "دليل للمراجعة", bank_recorded: "مسجل في البنك", operational_fee: "رسم تشغيلي مسجل",
};
export const STATEMENT_AVERAGE_REASONS = {
    statement_average_no_sales: "لا يحتوي هذا الكشف على مبيعات صالحة لاشتقاق متوسط العمولة.",
    statement_average_component_split_missing: "لا يفصل الكشف العمولة المتغيرة عن الرسم الثابت؛ لم يُفترض أي متوسط.",
    statement_average_sale_evidence_incomplete: "بيانات المبيعات أو العملة أو مكونات الرسوم غير مكتملة أو متعارضة؛ لم يُعتمد متوسط لهذا الكشف.",
    source_average_unavailable: "لا يتوفر متوسط معتمد من مبيعات مكتملة البيانات بهذه العملة.",
};
export const MAX_STATEMENT_FILE_BYTES = 10 * 1024 * 1024;
export const STATEMENT_REASONS = {
    statement_currency_missing: "عملة العملية غير موضحة في الكشف.",
    statement_amount_missing: "بعض المبالغ المطلوبة غير موضحة في الكشف.",
    statement_amount_invalid: "أحد مبالغ الكشف غير صالح للمطابقة.",
    statement_transfer_mismatch: "صافي التحويل لا يطابق مبلغ العملية وخصوماتها.",
    statement_deduction_mismatch: "إجمالي الخصومات لا يطابق الرسوم والضريبة.",
    statement_deduction_missing: "إجمالي الخصومات غير موضح.",
    statement_fee_component_mismatch: "تفاصيل الرسوم لا تطابق إجماليها.",
    statement_event_unsupported: "هذه العملية تحتاج مراجعة منفصلة.",
    statement_principal_sign_invalid: "إشارة مبلغ الدفع أو الرد لا تطابق نوع العملية.",
    statement_payment_method_missing: "طريقة الدفع غير موضحة في الكشف.",
    statement_fee_sign_invalid: "إشارة الرسم لا تطابق نوع العملية.",
    statement_currency_conflict: "توجد عملات متعارضة؛ لا يمكن جمعها في مطابقة واحدة.",
    statement_merchant_conflict: "هوية المتجر في الكشف تحتاج مراجعة.",
    statement_duplicate_row_ambiguous: "توجد أسطر مكررة لا يمكن تمييز عملياتها بأمان.",
    statement_order_reference_missing: "مرجع الطلب غير موضح في هذا السطر.",
    statement_order_reference_invalid: "مرجع الطلب غير صالح للمطابقة.",
    statement_event_date_missing: "تاريخ العملية غير موضح.",
    statement_event_date_invalid: "تاريخ العملية غير صالح للمطابقة.",
    statement_provider_unknown: "لم نتعرف على منصة الكشف. اختر ملف Excel الأصلي من تابي أو تمارا أو سلة.",
    statement_provider_ambiguous: "يحتوي الملف على علامات أكثر من منصة. اختر كشفًا واحدًا واضح المصدر.",
    statement_rows_missing: "لا توجد عمليات قابلة للقراءة في هذا الكشف.",
    statement_row_limit: "يتجاوز الكشف الحد المسموح: 10,000 سطر. قسّم الكشف قبل رفعه.",
    statement_sheet_limit: "يحتوي الملف على أوراق أكثر من الحد المسموح.",
    statement_file_invalid: "تعذر قراءة الملف. اختر ملف Excel أصليًا بصيغة .xlsx.",
    statement_header_ambiguous: "تتكرر عناوين مبالغ مالية في الكشف. يلزم ملف بعناوين واضحة قبل المطابقة.",
    statement_evidence_size_limit: "تفاصيل هذا الكشف أكبر من الحد المسموح للحفظ. قسّم الكشف إلى ملفات أصغر.",
};
const decimal = /^-?\d+\.\d{2}$/;
const text = value => typeof value === "string" && value.trim().length > 0;
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const count = value => Number.isSafeInteger(value) && value >= 0;
const amounts = ["principal_amount", "fee_net", "fee_vat", "fee_total"];
const missingActions = { record_missing_refund: "provider_refund", record_missing_capture: "provider_capture" };

// The server owns arithmetic. Keep its decimal strings exact, including zero;
// absent or malformed amounts are never converted into a financial zero.
export function statementMoney(value) {
    return typeof value === "string" && decimal.test(value)
        ? value.replace(/\B(?=(\d{3})+\.)/g, ",") : "غير متاح";
}

export function statementText(value) { return text(value) ? value : "غير متاح"; }

export function statementRate(value) {
    return typeof value === "string" && /^\d+\.\d{8}$/.test(value) ? value.replace(/0+$/, "").replace(/\.$/, "") : "غير متاح";
}

export function statementEstimatedAmount(value) {
    const match = typeof value === "string" ? /^(\d+)\.(\d{8})$/.exec(value) : null;
    return match ? `${match[1]}.${match[2].replace(/0+$/, "").padEnd(2, "0")}` : "غير متاح";
}

export function isStatementAverage(value, provider, currency) {
    return object(value) && value.available === true && value.provider === provider && value.native_currency === currency
        && value.calculation === "sale_variable_fees_before_vat_over_sale_principal"
        && text(value.statement_id) && statementRate(value.rate_percent) !== "غير متاح"
        && count(value.eligible_sale_rows) && value.eligible_sale_rows > 0
        && statementEstimatedAmount(value.fixed_amount_per_sale) !== "غير متاح"
        && ["principal_total", "fee_variable_net_total", "fee_fixed_net_total"].every(key => typeof value[key] === "string" && decimal.test(value[key]));
}

export function statementRowMessage(row) {
    return STATEMENT_REASONS[row.reason_code]
        || (typeof row.message === "string" && !/^[a-z][a-z0-9_]+$/.test(row.message) ? row.message : "راجع تفاصيل العملية قبل اعتمادها.");
}

function validProposedEffect(row, action) {
    const effect = row.proposed_effect;
    return object(effect) && effect.kind === missingActions[action] && effect.provider === row.provider
        && effect.native_currency === row.native_currency && text(effect.native_currency) && effect.bank_posting_allowed === false
        && ["principal_amount", "remaining_before", "remaining_after"].every(key => typeof effect[key] === "string" && decimal.test(effect[key]));
}

function validNativeAlias(row) {
    const alias = row.native_alias;
    return object(alias) && text(alias.id) && text(alias.reference)
        && typeof alias.amount === "string" && decimal.test(alias.amount) && alias.native_currency === row.native_currency
        && text(alias.event_date) && Number.isFinite(Date.parse(alias.event_date))
        && ["canonical_salla", "payment_refunds"].includes(alias.source);
}

export function statementConfirmationAction(row) {
    if (row.can_confirm !== true || !["missing", "different"].includes(row.status) || row.allowed_actions.length !== 1) return null;
    const action = row.allowed_actions[0];
    if (action === "accept_statement") return action;
    if (action === "link_native_refund") return row.kind === "refund" && row.status === "different"
        && row.existing.length === 1 && validNativeAlias(row) ? action : null;
    if (!Object.hasOwn(missingActions, action) || row.status !== "missing"
        || row.kind !== (action === "record_missing_refund" ? "refund" : "capture") || !validProposedEffect(row, action)) return null;
    if (row.existing.length === 0) return row.proposed_effect.employee_case_id == null ? action : null;
    const record = row.existing[0];
    return action === "record_missing_refund" && row.existing.length === 1 && record.kind === "customer_return"
        && record.financial_effect === "evidence_only" && record.id === row.proposed_effect.employee_case_id ? action : null;
}

export function statementErrorMessage(error, fallback = "تعذر إكمال العملية. أعد المحاولة عند الحاجة.") {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    if (STATEMENT_REASONS[code]) return STATEMENT_REASONS[code];
    if (STATEMENT_REASONS[detail?.message]) return STATEMENT_REASONS[detail.message];
    if (typeof detail?.message === "string" && !/^[a-z][a-z0-9_]+$/.test(detail.message)) return detail.message;
    if (error?.response?.status === 401 || error?.response?.status === 403) return "انتهت الجلسة أو لا تملك صلاحية إدارة كشوف المنصات.";
    if (error?.response?.status === 409) return "تغيرت المطابقة أو جلسة التشغيل. اقرأ الكشف مجددًا قبل الاعتماد.";
    if (error?.response?.status === 413) return "حجم الملف أكبر من 10 ميجابايت.";
    if ([400, 415, 422].includes(error?.response?.status)) return "تعذر قبول الكشف أو الاختيارات. راجع المدخلات وأعد المحاولة.";
    return fallback;
}

function validSnapshot(value) {
    return object(value) && text(value.session_scope) && typeof value.active === "boolean"
        && count(value.revision) && object(value.permissions)
        && typeof value.permissions.upload === "boolean" && typeof value.permissions.confirm === "boolean";
}

function validCounts(value) {
    return object(value) && STATEMENT_COUNTS.every(([key]) => count(value[key]));
}

export function isStatementHistory(value) {
    return validSnapshot(value) && Array.isArray(value.items) && value.items.every(item => object(item)
        && text(item.id) && Object.hasOwn(STATEMENT_PROVIDERS, item.provider)
        && count(item.row_count) && item.row_count <= 10000 && (item.counts === null || validCounts(item.counts)));
}

export function isStatementReview(value) {
    if (!validSnapshot(value) || value.bank_posting_allowed !== false || !object(value.statement)
        || !text(value.statement.id) || !Object.hasOwn(STATEMENT_PROVIDERS, value.statement.provider)
        || !text(value.preview_hash) || !Array.isArray(value.rows) || value.rows.length > 10000
        || value.statement.row_count !== value.rows.length || !validCounts(value.counts)) return false;
    const ids = new Set();
    return value.rows.every(row => {
        if (!object(row) || !text(row.id) || ids.has(row.id) || row.provider !== value.statement.provider
            || !Object.hasOwn(STATEMENT_STATUSES, row.status) || !Object.hasOwn(STATEMENT_KINDS, row.kind)
            || typeof row.can_confirm !== "boolean" || !Array.isArray(row.allowed_actions) || !Array.isArray(row.existing)
            || !amounts.every(key => row[key] === null || (typeof row[key] === "string" && decimal.test(row[key])))
            || !row.existing.every(record => object(record) && text(record.id))) return false;
        if (row.can_confirm && row.allowed_actions.some(action => Object.hasOwn(missingActions, action) || action === "link_native_refund") && !statementConfirmationAction(row)) return false;
        ids.add(row.id);
        return true;
    });
}

export function statementActionHref(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u001f]/.test(value)) return null;
    try {
        const url = new URL(value, window.location.origin);
        const paths = ["/operational-balances", "/operational-balances/customer-returns", "/operational-balances/customer-exchanges", "/operational-balances/daily-movements"];
        return url.origin === window.location.origin && paths.includes(url.pathname) ? `${url.pathname}${url.search}${url.hash}` : null;
    } catch { return null; }
}
