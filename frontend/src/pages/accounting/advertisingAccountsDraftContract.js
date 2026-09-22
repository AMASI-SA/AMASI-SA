// Display-only wire contract. No live adapter, permission grant or financial write.
export const MODEL_VERSION = "advertising-draft-v1";
export const PREFIX = "accounting.advertising.";
export const PERMISSION_KEYS = Object.freeze([
    "view", "settings.manage", "daily.view", "invoices.manage", "debts.review",
    "payments.record", "reconcile", "drafts.review", "journals.post",
].map((key) => PREFIX + key));
export const PROVIDERS = Object.freeze({snapchat: "Snapchat", meta: "Meta", tiktok: "TikTok", google: "Google"});
export const PAYMENT_MODES = Object.freeze({prepaid_wallet: "محفظة مسبقة الدفع", postpaid: "فاتورة آجلة ومديونية", direct_debit: "خصم مباشر من بنك أو صندوق"});
export const ROUTE_CONTRACT = Object.freeze({
    pageId: "advertising-accounts",
    shell: "/integrations-v2?workspace=financial&page=advertising-accounts",
    proposedAlias: "/accounting/advertising-accounts",
    registered: false,
    costSettings: "/ads-manager/cost-settings",
    openingBalances: "/integrations-v2?workspace=financial&page=opening-balances",
});
export const TABS = Object.freeze([
    ["overview", "نظرة عامة", "view"], ["daily", "الصرف اليومي", "daily.view"],
    ["wallet", "المحفظة والشحن", "view"], ["invoices", "الفواتير والمديونيات", "invoices.manage"],
    ["payments", "الدفعات", "payments.record"], ["reconciliation", "المطابقة", "reconcile"],
    ["journals", "القيود والمرفقات", "drafts.review"], ["audit", "سجل التدقيق", "drafts.review"],
].map(([id, label, permission]) => Object.freeze({id, label, permission: PREFIX + permission})));
export const CARDS = Object.freeze([
    ["today", "مصروف اليوم", "daily.view", "money"], ["month", "مصروف الشهر", "daily.view", "money"],
    ["wallets", "أرصدة المحافظ الإعلانية", "view", "money"], ["debts", "إجمالي المديونيات", "debts.review", "money"],
    ["paid_remaining", "المدفوع والمتبقي", "debts.review", "pair"], ["overdue", "فواتير متأخرة", "debts.review", "count"],
    ["unclosed", "أيام غير مغلقة", "daily.view", "count"], ["unlinked", "حسابات ناقصة الربط", "settings.manage", "count"],
    ["unmatched", "حركات تحتاج مطابقة", "reconcile", "count"],
].map(([id, label, permission, kind]) => Object.freeze({id, label, permission: PREFIX + permission, kind})));
export const STATUS_LABELS = Object.freeze({
    OPEN: "يوم مفتوح", DATA_COMPLETE: "بيانات مكتملة", DRAFT_CREATED: "مسودة غير مرحلة", RECONCILED: "تمت المطابقة",
    REVIEWED: "تمت المراجعة", POSTED: "قيد مرحّل للعرض فقط", INCOMPLETE_DATA: "بيانات غير مكتملة",
    MISSING_TIMEZONE: "المنطقة الزمنية مفقودة", BLOCKED_TIMEZONE_MISSING: "المنطقة الزمنية مفقودة",
    MISSING_FUNDING_SOURCE: "مصدر التمويل غير مربوط", MISSING_WALLET: "المحفظة غير مربوطة",
    INSUFFICIENT_WALLET_BALANCE: "رصيد المحفظة غير كافٍ", BANK_DIFFERENCE: "فرق عن حركة البنك أو الصندوق",
    LATE_SPEND_ADJUSTMENT: "تعديل صرف متأخر", DUPLICATE_SOURCE: "مصدر مكرر", NEEDS_REVIEW: "تحتاج مراجعة",
    draft: "مسودة", unpaid: "غير مدفوعة", partially_paid: "مدفوعة جزئيًا", paid: "مدفوعة بعد المطابقة",
    overdue: "متأخرة", disputed: "متنازع عليها", cancelled: "ملغاة", matched: "مطابقة", adjustment_draft: "مسودة فرق فقط",
    api: "API", upload: "رفع ملف", manual: "إدخال يدوي موثق", daily_spend: "الصرف اليومي", invoice: "الفاتورة",
});
export function canSee(grants, permission) {
    return Array.isArray(grants) && PERMISSION_KEYS.includes(permission)
        && permission !== PREFIX + "journals.post" && grants.includes(permission);
}
export function canOpenTab(grants, tab) {
    return !!tab && (canSee(grants, tab.permission) || (tab.id === "invoices" && canSee(grants, PREFIX + "debts.review")));
}
export function moneyText(value) {
    if (typeof value !== "string" || value.length > 80 || !/^-?\d+(\.\d{1,12})?$/.test(value)) return "غير متاح";
    const [whole, decimal = ""] = value.split(".");
    return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${decimal.padEnd(2, "0")}`;
}
export function countText(value) {
    return Number.isSafeInteger(value) && value >= 0 ? String(value) : "غير متاح";
}
export function statusText(value) {
    return typeof value === "string" && value ? (STATUS_LABELS[value] || value) : "غير متاح";
}
export function viewState(ownerId, model) {
    const blocked = (code) => ({status: "error", code, accounts: [], summary: null});
    if (!ownerId) return blocked("OWNER_SCOPE_MISSING");
    if (!model) return {status: "dependency_blocked", accounts: [], summary: null};
    if (model.status === "error") return blocked("SOURCE_UNAVAILABLE");
    if (model.status !== "ready") return {status: model.status === "loading" ? "loading" : "dependency_blocked", accounts: [], summary: null};
    if (model.owner_id !== ownerId) return blocked("OWNER_SCOPE_MISMATCH");
    if (model.version !== MODEL_VERSION || !Array.isArray(model.accounts)) return blocked("INVALID_DISPLAY_CONTRACT");
    const identities = new Set(), references = new Set();
    for (const row of model.accounts) {
        if (!row || row.owner_id !== ownerId) return blocked("OWNER_SCOPE_MISMATCH");
        if (!Object.hasOwn(PROVIDERS, row.ad_provider) || typeof row.external_account_id !== "string" || !row.external_account_id.trim()
            || typeof row.linked_account_ref !== "string" || !row.linked_account_ref.trim()) return blocked("ACCOUNT_NOT_LINKED");
        const key = JSON.stringify([ownerId, row.ad_provider, row.external_account_id]);
        if (identities.has(key) || references.has(row.linked_account_ref)) return blocked("DUPLICATE_SOURCE");
        identities.add(key); references.add(row.linked_account_ref);
    }
    return {status: "ready", accounts: model.accounts, summary: model.summary || null};
}
export function detailFor(model, ownerId, ref) {
    if (!ref || !model?.details || !Object.hasOwn(model.details, ref)) return null;
    const detail = model.details[ref];
    return detail?.owner_id === ownerId && detail?.linked_account_ref === ref ? detail : null;
}
export function scopedRows(detail, key) {
    const rows = detail?.[key];
    return Array.isArray(rows) && rows.every((row) => row && row.owner_id === detail.owner_id
        && row.linked_account_ref === detail.linked_account_ref) ? rows : null;
}
