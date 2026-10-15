import { useEffect, useId, useRef } from "react";
import "./accountingUI.css";

// Formatting only. No balances, exchange rates, netting or journal legs are computed here.
export function formatAccountingMoney(value, currency = "SAR") {
    if ((typeof value !== "number" && typeof value !== "string") || String(value).trim() === "" || !Number.isFinite(Number(value))) return "—";
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency === "SAR" ? "ر.س" : currency}`;
}
export function MoneyDisplay({ value, currency = "SAR" }) {
    const text = formatAccountingMoney(value, currency);
    return <bdi className="ac-money" dir="ltr" aria-label={text === "—" ? "المبلغ غير متاح" : undefined}>{text}</bdi>;
}
export function AccountingPageHeader({ title, description, children, level = 2 }) {
    const Heading = level === 1 ? "h1" : "h2";
    return <header className={`ac-header${level === 1 ? "" : " ac-header-section"}`}><div>{level === 1 && <p className="ac-eyebrow">ميزان / المحاسبة</p>}<Heading>{title}</Heading>{description && <p>{description}</p>}</div><div className="ac-actions">{children}</div></header>;
}
export function FinancialSummaryCards({ items }) {
    return <div className="ac-summary">{items.map(item => <article key={item.label}><p>{item.label}</p><strong>{item.money ? <MoneyDisplay value={item.value} /> : item.value ?? "—"}</strong><small>{item.hint}</small></article>)}</div>;
}
export function BalanceBreakdown({ outstanding, advance, available = false }) {
    return <FinancialSummaryCards items={[
        { label: "المستحق", value: available ? outstanding : null, money: true, hint: "التزام مستقل" },
        { label: "الدفعة المقدمة", value: available ? advance : null, money: true, hint: "أصل مستقل؛ لا يُخصم من المستحق هنا" },
    ]} />;
}
const STATUS_LABELS = {
    available: "متاح", not_ready: "غير جاهز · not_ready", BLOCKED_BY_BACKEND: "BLOCKED_BY_BACKEND / not_ready", needs_opening_balance: "بانتظار رصيد افتتاحي معتمد",
    draft: "مسودة", needs_review: "تحتاج مراجعة", matched: "تمت المطابقة", ready_for_review: "جاهزة للمراجعة",
    reviewed: "تمت المراجعة", posting: "جاري الترحيل", posted: "مرحّلة", rejected: "معادة للمعالجة", reversed: "معكوسة",
    unclassified: "غير مصنفة", accounting_posted: "مرحّلة محاسبيًا", provider_receipt_created: "مرتبطة بإيصال",
    pending_provider_receipt: "بانتظار إيصال", readonly: "للقراءة فقط", unavailable: "غير متاح",
};
export function StatusBadge({ value, label }) {
    const tone = ["available", "posted", "accounting_posted"].includes(value) ? "success" : ["rejected", "reversed"].includes(value) ? "danger" : ["needs_review", "not_ready", "unavailable", "BLOCKED_BY_BACKEND", "needs_opening_balance"].includes(value) ? "warning" : "neutral";
    return <span className={`ac-status ac-status-${tone}`}>{label || STATUS_LABELS[value] || value || "غير متاح"}</span>;
}
export function EvidenceBadge({ reference }) {
    return <span className="ac-evidence">{reference ? <>مرجع الدليل: <bdi>{reference}</bdi></> : "الدليل غير متاح"}</span>;
}
export function EmptyState({ title = "لا توجد نتائج", description = "جرّب تغيير الفلاتر أو إعادة تعيين البحث." }) {
    return <div className="ac-empty" role="status"><span aria-hidden="true">▤</span><h3>{title}</h3><p>{description}</p></div>;
}
export function ErrorState({ message = "تعذر تحميل البيانات", onRetry }) {
    return <div className="ac-error" role="alert"><strong>{message}</strong><p>لم تُستبدل البيانات بمصدر آخر.</p>{onRetry && <button type="button" onClick={onRetry}>إعادة المحاولة</button>}</div>;
}
export function AccountingSkeleton({ label = "جاري تحميل البيانات…" }) {
    return <div className="ac-skeleton" role="status" aria-label={label}><span className="sr-only">{label}</span>{[0, 1, 2, 3].map(i => <div key={i} aria-hidden="true" />)}</div>;
}
export function AccountingFilters({ search, onSearch, onReset, children, scope = "البحث داخل النتائج المحمّلة فقط" }) {
    return <div className="ac-filters"><div className="ac-filter-fields"><label className="ac-search">بحث واضح في السجل<input type="search" value={search} onChange={event => onSearch(event.target.value)} placeholder="ابحث بالاسم أو المعرف أو المرجع" /></label>{children}<button type="button" onClick={onReset}>إعادة تعيين</button></div><p>{scope}</p></div>;
}
export function EntityPicker({ label = "الحساب", value, onChange, options }) {
    return <label>{label}<select value={value} onChange={event => onChange(event.target.value)}><option value="">الكل</option>{options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
}
export function AccountingPagination({ page, count, pageSize = 20, onChange }) {
    const pages = Math.max(1, Math.ceil(count / pageSize));
    return <nav className="ac-pagination" aria-label="صفحات النتائج المحملة"><span>{count ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, count)}` : "0"} من {count} سجل محمّل</span><div><button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>السابق</button><span> {page} / {pages} </span><button type="button" disabled={page >= pages} onClick={() => onChange(page + 1)}>التالي</button></div></nav>;
}
export function AccountingDetails({ title, open, onClose, children }) {
    const trigger = useRef(null);
    const dialog = useRef(null);
    const titleId = useId();
    const descriptionId = useId();
    useEffect(() => {
        if (open) {
            trigger.current = document.activeElement;
            dialog.current.showModal();
        } else if (dialog.current.open) {
            dialog.current.close();
            trigger.current?.focus?.();
        }
    }, [open]);
    return <dialog ref={dialog} className="ac-detail" dir="rtl" aria-labelledby={titleId} aria-describedby={descriptionId} onCancel={event => { event.preventDefault(); onClose(); }}><div className="ac-detail-heading"><h2 id={titleId}>{title}</h2><button type="button" onClick={onClose} aria-label="إغلاق التفاصيل">إغلاق ×</button></div><p id={descriptionId}>تفاصيل السجل كما أعادها المصدر، دون إعادة حساب الأرصدة.</p>{open && children}</dialog>;
}
export function AuditTimeline({ items = [] }) {
    return items.length ? <ol className="ac-timeline">{items.map((item, index) => <li key={item.id || index}><strong>{item.label}</strong><time dir="ltr">{item.at || "غير متاح"}</time>{item.detail && <p>{item.detail}</p>}</li>)}</ol> : <EmptyState title="لا يتوفر سجل تغييرات" description="لم يُرجع المصدر أحداثًا لهذا السجل." />;
}
function PosMovementDestination({ row }) {
    const destination = [row.metadata?.destination, row.metadata?.pos_destination].find(value =>
        value?.destination_kind === "pos_receivable" && value.entity_type === "asset" &&
        value.sub_account === "other_receivable" && value.entity_id);
    if (!destination) return null;
    return <small data-testid="pos-movement-destination"><strong>ذمة شبكة POS: {destination.display_name || "الاسم غير متاح"}</strong><br /><bdi>{destination.entity_type}/{destination.entity_id}/{destination.sub_account}</bdi></small>;
}

export function JournalTable({ rows, label = "قيود ميزان 2", accountLabels = {}, onSelect }) {
    const captionId = useId();
    return <div className="ac-table-scroll" tabIndex={0} role="region" aria-labelledby={captionId}><table className="ac-table" aria-label={label}><caption id={captionId}>{label}</caption><thead><tr>{["التاريخ المحاسبي", "مجموعة القيد", "الحساب", "مدين", "دائن", ...(onSelect ? ["التفاصيل"] : [])].map(title => <th scope="col" key={title}>{title}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={row.id || index}><td><bdi>{journalDate(row) || "غير متاح"}</bdi></td><td><bdi>{row.txn_group_id || "غير متاح"}</bdi></td><td>{accountLabels[row.sub_account] || row.sub_account || row.entity_type || "غير متاح"}<small><bdi>{row.entity_id}</bdi></small><PosMovementDestination row={row} /></td><td><MoneyDisplay value={row.side === "debit" ? row.amount : null} /></td><td><MoneyDisplay value={row.side === "credit" ? row.amount : null} /></td>{onSelect && <td><button type="button" onClick={() => onSelect(row)} aria-label={`تفاصيل القيد ${row.txn_group_id || row.id}`}>عرض القيد</button></td>}</tr>)}</tbody></table></div>;
}
export function journalDate(row) { return row.effective_at || row.metadata?.accounting_at || row.metadata?.recognized_at || ""; }
