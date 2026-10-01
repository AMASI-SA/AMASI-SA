import { useEffect, useState } from "react";
import { AccountingFilters, AccountingPageHeader, AccountingSkeleton, EmptyState, ErrorState, MoneyDisplay, StatusBadge } from "../AccountingUI";
import { ADVERTISING_GAPS, ADVERTISING_PLATFORMS, advertisingReadFailure, readAdvertisingContext, readAdvertisingDueItems, readAdvertisingSource } from "./advertisingAdapter";

function Blocked({ reason, children }) {
    return <aside className="h2-blocked"><StatusBadge value="BLOCKED_BY_BACKEND" /><p>{children}</p><small><bdi>{reason}</bdi></small></aside>;
}
function ReadFailure({ error, onRetry }) {
    const failure = advertisingReadFailure(error);
    return failure.blocked ? <Blocked reason={failure.reason}>القدرة الإعلانية غير جاهزة من المصدر. لا تمثل هذه الحالة رصيدًا صفريًا.{onRetry && <button type="button" onClick={onRetry}>إعادة المحاولة</button>}</Blocked> : <ErrorState message="تعذر تحميل بيانات الإعلانات الأصلية" onRetry={onRetry} />;
}
function DailySource({ account }) {
    const [date, setDate] = useState("");
    const [request, setRequest] = useState(null);
    const [state, setState] = useState({});
    useEffect(() => {
        if (!request) return undefined;
        let active = true;
        setState({ loading: true });
        readAdvertisingSource(account, request.date).then(data => { if (active) setState({ data }); }, error => { if (active) setState({ error }); });
        return () => { active = false; };
    }, [account, request]);
    return <div>
        <form onSubmit={event => { event.preventDefault(); setRequest({ date }); }} className="ac-filters">
            <label>تاريخ الإنفاق بتوقيت الحساب<input type="date" required value={date} onChange={event => { setDate(event.target.value); setState({}); setRequest(null); }} /></label>
            <button type="submit" disabled={!date || state.loading}>عرض مصدر الإنفاق</button>
        </form>
        {state.loading && <AccountingSkeleton label="جاري تحميل مصدر الإنفاق" />}
        {state.error && <ReadFailure error={state.error} onRetry={() => setRequest({ date })} />}
        {state.data && <dl className="ac-timeline">
            <div><dt>تاريخ المصدر</dt><dd><bdi>{state.data.business_date}</bdi></dd></div>
            <div><dt>الإنفاق الأصلي من المصدر</dt><dd><MoneyDisplay value={state.data.original_amount} currency={state.data.original_currency} /></dd></div>
            <div><dt>حالة الإثبات</dt><dd>مصدر إنفاق فقط؛ لا يثبت اكتمال الترحيل المحاسبي.</dd></div>
            <div><dt>مراجعة المصدر</dt><dd><bdi>{state.data.source_revision}</bdi></dd></div>
            <div><dt>متطلب الترحيل</dt><dd><bdi>{state.data.missing_contract_reason || "غير متاح"}</bdi></dd></div>
        </dl>}
    </div>;
}
export default function AdvertisingPanel() {
    const [version, setVersion] = useState(0);
    const [context, setContext] = useState({ loading: true });
    const [due, setDue] = useState({ loading: true });
    const [selected, setSelected] = useState("");
    const [search, setSearch] = useState("");
    useEffect(() => {
        let active = true;
        setContext({ loading: true }); setDue({ loading: true }); setSelected("");
        readAdvertisingContext().then(data => { if (active) setContext({ data }); }, error => { if (active) setContext({ error }); });
        readAdvertisingDueItems().then(items => { if (active) setDue({ items }); }, error => { if (active) setDue({ error }); });
        return () => { active = false; };
    }, [version]);
    const retry = () => setVersion(value => value + 1);
    const key = account => `${account.platform}:${account.integration_account_id}`;
    const account = context.data?.items.find(item => key(item) === selected);
    const visible = context.data?.items.filter(item => [item.display_name, item.integration_account_id, item.platform_account_id, item.platform, ADVERTISING_PLATFORMS[item.platform]].some(value => String(value || "").toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())));
    return <section dir="rtl" aria-label="الإعلانات اليومية">
        <AccountingPageHeader title="الإعلانات اليومية" description="الحسابات وجاهزية السياسات من ميزان 2. الإعداد الجاهز لا يعني أن الإنفاق رُحّل."><button type="button" onClick={retry}>تحديث الإعلانات</button></AccountingPageHeader>
        {context.loading && <AccountingSkeleton />}
        {context.error && <ReadFailure error={context.error} onRetry={retry} />}
        {context.data && (context.data.items.length ? <>
            <AccountingFilters search={search} onSearch={setSearch} onReset={() => setSearch("")} scope="البحث داخل الحسابات الإعلانية الأصلية المحمّلة بالاسم أو المعرف أو المنصة." />
            {!visible.length && <EmptyState title="لا توجد حسابات تطابق البحث" description="امسح البحث لعرض الحسابات المحمّلة." />}
            <div className="ac-table-scroll" role="region" tabIndex={0} aria-label="حسابات الإعلانات"><table className="ac-table"><thead><tr><th>الحساب</th><th>العملة</th><th>جاهزية الإعداد</th><th>الإنفاق التلقائي</th><th>التفاصيل</th></tr></thead><tbody>{visible.map(item => <tr key={key(item)}>
                <td>{ADVERTISING_PLATFORMS[item.platform]} · {item.display_name || item.integration_account_id}</td>
                <td><bdi>{item.currency || "غير متاح"}</bdi></td>
                <td><StatusBadge value={item.readiness === "SETUP_READY" ? "available" : "BLOCKED_BY_BACKEND"} label={item.readiness === "SETUP_READY" ? "الإعداد جاهز" : undefined} /><small><bdi>{item.missing_contract_reason}</bdi></small></td>
                <td><StatusBadge value={item.daily_spend_readiness === "AUTOMATIC_POLICY_CONFIGURED" ? "readonly" : "BLOCKED_BY_BACKEND"} label={item.daily_spend_readiness === "AUTOMATIC_POLICY_CONFIGURED" ? "السياسة مضبوطة؛ ليست إثبات ترحيل" : undefined} /><small><bdi>{item.daily_spend_gap}</bdi></small></td>
                <td><button type="button" aria-expanded={selected === key(item)} onClick={() => setSelected(selected === key(item) ? "" : key(item))}>مراجعة {item.display_name || item.integration_account_id}</button></td>
            </tr>)}</tbody></table></div>
            {account && <div className="ac-section" aria-label="تفاصيل الحساب الإعلاني">
                <h3>{account.display_name || account.integration_account_id}</h3>
                <dl className="ac-timeline">
                    <div><dt>محفظة الدفع المقدم · هوية الربط</dt><dd><bdi>{account.wallet_binding || "غير متاح"}</bdi></dd></div>
                    <div><dt>المستحق · هوية الربط</dt><dd><bdi>{account.payable_binding || "غير متاح"}</bdi></dd></div>
                    <div><dt>الرصيد المرحّل بالعملة الأصلية للمحفظة</dt><dd>{account.original_wallet?.currency ? <MoneyDisplay value={account.original_wallet.posted_original_balance} currency={account.original_wallet.currency} /> : "غير متاح"}</dd></div>
                    <div><dt>دليل افتتاح المحفظة</dt><dd>{account.original_wallet?.opening_confirmed === true ? "دليل مؤكد؛ لا يعني أنه رُحّل" : "غير متاح"}</dd></div>
                    <div><dt>جدول السياسة</dt><dd><bdi>{account.run_at || "غير متاح"} · {account.schedule_timezone || "غير متاح"}</bdi></dd></div>
                </dl>
                <DailySource key={selected} account={account} />
                <Blocked reason={account.bank_movement_gap || ADVERTISING_GAPS.bank}>التمويل والتسوية البنكية ينتظران اكتمال إثبات الحركة وربط الترحيل.</Blocked>
            </div>}
        </> : <EmptyState title="لا توجد حسابات إعلانية أصلية" description={context.data.missing_contract_reason || "لم يُرجع المصدر حسابات ميزان 2."} />)}
        <h3>الأيام المستحقة للمعالجة التلقائية</h3>
        <p>قائمة محدودة إلى 100 يوم من Backend؛ غياب اليوم لا يثبت ترحيله أو CLOSED_ZERO.</p>
        {due.loading && <AccountingSkeleton />}
        {due.error && <ReadFailure error={due.error} onRetry={retry} />}
        {due.items && (due.items.length ? <ul className="ac-timeline">{due.items.map((item, index) => <li key={`${key(item)}:${item.business_date}:${index}`}><strong>{ADVERTISING_PLATFORMS[item.platform]} · <bdi>{item.integration_account_id}</bdi></strong><p><bdi>{item.business_date}</bdi> · مستحق للمعالجة، ليس إثبات تحصيل أو ترحيل</p><small><bdi>{item.due_at || "غير متاح"}</bdi></small></li>)}</ul> : <EmptyState title="لا توجد أيام في قائمة المعالجة" description="هذه القائمة لا تعرض سجل الأيام المغلقة أو المرحّلة." />)}
        <Blocked reason={ADVERTISING_GAPS.postedHistory}>حالات الترحيل الفعلية وCLOSED_ZERO تحتاج عقد قراءة لسجل المعالجة.</Blocked>
        <Blocked reason={ADVERTISING_GAPS.fx}>جاهزية FX لكل يوم تنتظر عقد قراءة أصليًا؛ لا يُستنتج سعر الصرف من الواجهة.</Blocked>
        <Blocked reason={ADVERTISING_GAPS.balances}>رصيد المحفظة بالريال والمستحق ينتظران عقد الأرصدة الأصلي؛ لا تُجمع الحركات هنا.</Blocked>
    </section>;
}
