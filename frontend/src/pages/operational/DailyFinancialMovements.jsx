import { useEffect, useRef, useState } from "react";
import { useOptionalAuth } from "../../context/AuthContext";
import { addDaysISO, formatRiyadhDateTime, todaySA } from "../../lib/dates";
import { operationalApi, messageFor } from "./api";
import { formatPositionMoney } from "./financialPositionPresentation";
import "./financialPosition.css";

const unavailable = "غير متاح";
const directions = { incoming: "وارد", outgoing: "صادر", noncash: "تحصيل شبكة — لم يصل للبنك" };
const kinds = { transfer: "تحويل بين حسابات", customer_refund: "استرجاع للعميل", pos_collection_approved: "اعتماد تحصيل شبكة", movement: "حركة مالية", payment: "سداد", receipt: "تحصيل" };
export function dailyRange(preset, today = todaySA()) {
    const days = { today: 0, yesterday: 1, week: 6, month: 29 };
    return { from_date: addDaysISO(today, -days[preset]), to_date: preset === "yesterday" ? addDaysISO(today, -1) : today };
}
const dateText = value => value && !Number.isNaN(Date.parse(value)) ? formatRiyadhDateTime(value) : unavailable;
const money = (value, currency) => value == null ? unavailable : `${formatPositionMoney(value)} ${currency || ""}`;
function Balance({ row, party = false }) {
    const prefix = party ? "counterparty" : "account";
    const value = row[`${prefix}_balance_after`];
    if (row[`${prefix}_balance_status`] === "not_applicable") return "لا ينطبق";
    if (row[`${prefix}_balance_status`] !== "available" || value == null) return unavailable;
    const negative = String(value).startsWith("-");
    const nonzero = !/^-?0(?:\.0+)?$/.test(String(value));
    return <><bdi>{party && nonzero && !negative ? "+" : ""}{money(value, row[`${prefix}_balance_currency`])}</bdi>
        {party && <small>{nonzero ? negative ? "علينا للجهة" : "لنا على الجهة" : "لا رصيد مستحق"}</small>}
        {party && row.counterparty_balance_basis === "confirmed_obligations" && <small>المستحقات المؤكدة فقط</small>}</>;
}

export default function DailyFinancialMovements() {
    const { user } = useOptionalAuth() || {};
    const identity = `${user?.id || ""}:${user?.owner_id || user?.created_by || ""}:${user?.role || ""}`;
    const [preset, setPreset] = useState("today");
    const [draft, setDraft] = useState(() => dailyRange("today"));
    const [query, setQuery] = useState(() => ({ ...dailyRange("today"), page: 1, page_size: 10 }));
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [refresh, setRefresh] = useState(0);
    const sequence = useRef(0);
    const queryKey = JSON.stringify(query);
    const data = result?.identity === identity && result?.queryKey === queryKey ? result.data : null;
    useEffect(() => {
        const current = ++sequence.current;
        setResult(null); setError("");
        if (!user?.id) { setLoading(false); return undefined; }
        setLoading(true);
        operationalApi.dailyFinancialMovements(query).then(value => {
            if (current !== sequence.current) return;
            if (!Array.isArray(value?.items) || value.page_size !== 10 || value.page !== query.page ||
                value.from_date !== query.from_date || value.to_date !== query.to_date || value.timezone !== "Asia/Riyadh") {
                throw new Error("invalid daily movement response");
            }
            setResult({ identity, queryKey, data: value });
        }).catch(reason => {
            if (current === sequence.current) setError(reason?.response?.status === 403 ? "لا توجد صلاحية لعرض هذه الحركات أو تغيرت صلاحيات الحسابات." :
                reason?.response?.status === 409 ? "تغيرت الحركات أو الصلاحيات؛ اضغط إعادة تحميل الصفحة الأولى." : messageFor(reason));
        }).finally(() => { if (current === sequence.current) setLoading(false); });
        return () => { sequence.current += 1; };
    }, [identity, queryKey, refresh]); // Each response is bound to its authenticated actor and range.
    const firstPage = range => { setQuery({ ...range, page: 1, page_size: 10 }); setRefresh(value => value + 1); };
    const choosePreset = event => {
        const next = event.target.value; setPreset(next);
        if (next !== "custom") { const range = dailyRange(next); setDraft(range); firstPage(range); }
    };
    const apply = event => {
        event.preventDefault();
        if (!draft.from_date || !draft.to_date || draft.from_date > draft.to_date) {
            setError("اختر تاريخ بداية ونهاية صحيحين وبالترتيب."); return;
        }
        firstPage(draft);
    };
    return <main className="ofp-page" dir="rtl" aria-label="الحركات المالية اليومية">
        <header className="ofp-toolbar"><h1>الحركات المالية اليومية</h1>
            <a className="ofp-button" href="/operational-balances">الأرصدة التشغيلية</a>
            <button type="button" className="ofp-button" onClick={() => firstPage({ from_date: query.from_date, to_date: query.to_date })} disabled={loading}>إعادة تحميل الصفحة الأولى</button>
        </header>
        <p>الحسابات المصرح لك بها فقط. الأوقات بتوقيت الرياض. الموجب في رصيد الجهة لنا والسالب علينا.</p>
        <form onSubmit={apply} className="ofp-delivery-filters" aria-label="فترة الحركات">
            <label>الفترة<select aria-label="الفترة" value={preset} onChange={choosePreset} disabled={loading}>
                <option value="today">اليوم</option><option value="yesterday">أمس</option><option value="week">آخر 7 أيام</option>
                <option value="month">آخر 30 يومًا</option><option value="custom">فترة محددة</option>
            </select></label>
            <label>من تاريخ<input aria-label="من تاريخ" type="date" value={draft.from_date} disabled={preset !== "custom" || loading} onChange={event => setDraft({ ...draft, from_date: event.target.value })} required /></label>
            <label>إلى تاريخ<input aria-label="إلى تاريخ" type="date" value={draft.to_date} disabled={preset !== "custom" || loading} onChange={event => setDraft({ ...draft, to_date: event.target.value })} required /></label>
            <button className="ofp-button" disabled={preset !== "custom" || loading}>عرض الفترة</button>
        </form>
        {loading && <p role="status">جارٍ تحميل الحركات…</p>}
        {error && <p role="alert">{error}</p>}
        {data && <>
            {data.coverage?.complete !== true && <p className="ofp-status">بيانات بعض الحركات غير مكتملة؛ المبالغ غير المتاحة لا تُعرض كصفر.</p>}
            {(data.coverage?.notes || []).map((note, index) => <p key={index}>{note}</p>)}
            <section aria-label="إجماليات الفترة" className="ofp-table-card"><h2>إجماليات الفترة للحسابات المصرح بها</h2>
                {Array.isArray(data.totals) ? <table><thead><tr><th>العملة</th><th>الوارد</th><th>الصادر</th><th>تحصيل شبكة غير بنكي</th></tr></thead><tbody>
                    {data.totals.map(total => <tr key={total.currency}><td>{total.currency}</td><td><bdi>{money(total.incoming, total.currency)}</bdi></td><td><bdi>{money(total.outgoing, total.currency)}</bdi></td><td><bdi>{money(total.noncash, total.currency)}</bdi></td></tr>)}
                </tbody></table> : <p>إجماليات الفترة غير متاحة من المصدر.</p>}
                <p>عدد الحركات ضمن الفترة والصلاحيات: {data.total}. لا تجمع العملات المختلفة، والتحويل بين حسابين يظهر أثر كل حساب مصرح به.</p>
            </section>
            <section className="ofp-table-card" aria-label="سجل الحركات"><div className="ofp-table-scroll"><table><thead><tr>
                <th>الموظف</th><th>التاريخ والساعة — الرياض</th><th>البنك أو الصندوق</th><th>نوع الحركة</th><th>المبلغ</th><th>الجهة</th><th>رصيد الحساب بعد الحركة</th><th>رصيد الجهة بعد الحركة</th>
            </tr></thead><tbody>{data.items.map(row => <tr key={row.id} data-testid="daily-movement-row">
                <td>{row.employee_name || unavailable}{row.employee_source === "current_profile" && <small>الاسم من ملف الموظف الحالي</small>}</td>
                <td><strong>{dateText(row.activity_at)}</strong><small>وقت التسجيل: {dateText(row.recorded_at)}</small><small>وقت الحدث الأصلي: {dateText(row.occurred_at)}</small>{row.balance_as_of && <small>حفظ الرصيد: {dateText(row.balance_as_of)}</small>}</td>
                <td>{row.account_name || unavailable}</td><td>{kinds[row.kind] || "حركة مالية"}<small>{directions[row.direction] || unavailable}</small></td>
                <td><bdi>{money(row.amount, row.currency)}</bdi></td><td>{row.counterparty_name || unavailable}{row.order_number && <small>الطلب: {row.order_number}</small>}{(row.notes || []).map((note, index) => <small key={index}>{note}</small>)}</td>
                <td><Balance row={row} /></td><td><Balance row={row} party /></td>
            </tr>)}</tbody></table></div>
                {!data.items.length && <p>{data.financial_source === "unavailable" ? "مصدر الحركات غير متاح حاليًا." : "لا توجد حركات ضمن الفترة والحسابات المصرح بها."}</p>}
                <nav aria-label="صفحات الحركات المالية"><button className="ofp-button" disabled={loading || !data.has_previous} onClick={() => setQuery({ ...query, page: data.page - 1, snapshot: data.snapshot })}>الصفحة السابقة</button>
                    <span> الصفحة {data.page} من {Math.max(1, data.total_pages)} · 10 حركات في الصفحة </span>
                    <button className="ofp-button" disabled={loading || !data.has_next} onClick={() => setQuery({ ...query, page: data.page + 1, snapshot: data.snapshot })}>الصفحة التالية</button></nav>
            </section>
        </>}
    </main>;
}
