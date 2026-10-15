import { useEffect, useMemo, useState } from "react";
import api from "../../lib/api";
import {
    AccountingPageHeader, AccountingFilters, AccountingSkeleton, AccountingPagination,
    AccountingDetails, EntityPicker, JournalTable, MoneyDisplay, StatusBadge,
    EmptyState, ErrorState, FinancialSummaryCards, EvidenceBadge, journalDate,
} from "./AccountingUI";

const BASE = "/financial-provider-apps/accounting-module/reports";
const REPORTS = [["financial-position", "المركز المالي"], ["trial-balance", "ميزان المراجعة"], ["journals", "القيود اليومية"]];
const DOMAINS = [
    ["", "كل الحسابات"], ["supplier", "المورد"], ["employee", "الموظف"], ["courier", "شركة الشحن"],
    ["store_driver", "الموصل"], ["ad_account", "المنصات الإعلانية"], ["tax", "الضرائب"],
    ["expense", "المصروفات"], ["liability", "الالتزامات"], ["asset", "الأصول والمخزون"],
];
const LABELS = {
    banks: "البنوك والصناديق", payment_platforms_remaining: "ذمم مزودي الدفع", input_vat: "ضريبة المدخلات",
    recoverable: "ضريبة قابلة للاسترداد", customer_refund_payable: "التزام استرداد العميل", customer_advance: "تحصيلات العملاء المقدمة",
    sales_vat_payable: "ضريبة المبيعات المستحقة", employee_advance: "سلف الموظفين", employee_custody: "عهد الموظفين",
    external_receivable: "ذمم مدينة أخرى", courier_cod_receivable: "تحصيلات شركات الشحن", store_driver_cod_receivable: "تحصيلات موصلي المتجر",
    salaries_unpaid: "رواتب مستحقة", supplier_payable: "مستحقات الموردين", supplier_advance: "دفعات مقدمة للموردين",
    courier_payable: "مستحقات الشحن", store_driver_payable: "مستحقات الموصلين", external_payable: "ذمم دائنة أخرى",
    ad_accounts_unpaid: "مستحقات الإعلانات", ad_account_prepaid: "محافظ إعلانية مقدمة",
    total_assets: "إجمالي الأصول", total_liabilities: "إجمالي الالتزامات", net_position: "صافي المركز المالي",
    main: "الحساب الرئيسي", payable: "مستحق", advance: "دفعة مقدمة / سلفة", custody: "عهدة", salary_payable: "راتب مستحق",
    balance: "محفظة مقدمة", debt: "مستحق علينا", cod_receivable: "تحصيل مستحق لنا", delivery_fee_payable: "أجرة مستحقة",
};
const GAPS = {
    supplier: "كشف قيود فقط. فواتير المورد الأصلية وتخصيص السداد والدفعة المقدمة تنتظر اكتمال عقد المورد الموحد.",
    employee: "كشف قيود فقط. الراتب الحالي وتاريخ تغييره والمدفوع عن كل شهر تنتظر عقد الموظف الموحد؛ الرصيد ليس إثبات صرف الراتب.",
    courier: "التحصيل المستحق وأجرة الشحن حسابان منفصلان. مرشحو الشحن لا يمثلون رصيدًا، والترحيل يخضع لضوابطه الحالية.",
    store_driver: "هذا كشف محاسبي فقط. التحصيل التشغيلي لدى الموصل لا يُحوّل إلى رصيد محاسبي هنا.",
    ad_account: "المحفظة المقدمة والمستحق منفصلان. ربط الهوية بـ Snap أو Meta أو TikTok أو Google Ads والصرف اليومي والتمويل غير متاح في عقد التقرير.",
    tax: "ضريبة المبيعات وضريبة المدخلات تُعرضان كما وردتا في القيود. فترات الإقرار وملفات الأدلة الضريبية غير متاحة هنا.",
    expense: "القيود المحملة فقط؛ لا يُنشأ تقدير للمصروف أو جدول استحقاق دوري من هذه البيانات.",
    liability: "لا يُستنتج جدول التزامات دورية أو تأمينات أو دفعات قادمة من رصيد الحساب.",
    asset: "قيمة المخزون لكل منتج أو مكوّن غير متاحة في هذا العقد. لا تُحسب من الكمية والتكلفة التشغيلية.",
};
const accountKey = row => JSON.stringify([row.entity_type, row.entity_id, row.sub_account || ""]);
const textMatches = (row, search) => !search || [row.entity_type, row.entity_id, row.sub_account, row.txn_group_id, row.entry_type, row.metadata?.description, row.metadata?.reference].some(value => String(value || "").toLocaleLowerCase().includes(search.toLocaleLowerCase()));
function AmountList({ title, values }) {
    return <section className="rounded-xl border border-slate-200 bg-white p-5"><h3 className="font-bold">{title}</h3>
        <dl>{Object.entries(values || {}).map(([key, value]) => <div key={key} className="flex flex-wrap justify-between gap-3 border-b border-slate-100 py-3 text-sm last:border-0">
            <dt>{LABELS[key] || key}</dt><dd><MoneyDisplay value={value} /></dd>
        </div>)}</dl></section>;
}
export default function AccountingReports({ initialDomain = "" }) {
    const [report, setReport] = useState(initialDomain ? "trial-balance" : "financial-position");
    const [domain, setDomain] = useState(DOMAINS.some(([key]) => key === initialDomain) ? initialDomain : "");
    const [date, setDate] = useState("");
    const [asOf, setAsOf] = useState("");
    const [revision, setRevision] = useState(0);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");
    const [account, setAccount] = useState("");
    const [source, setSource] = useState("");
    const [page, setPage] = useState(1);
    const [selectedGroup, setSelectedGroup] = useState(null);
    useEffect(() => {
        let active = true;
        setLoading(true); setData(null); setError(""); setSelectedGroup(null); setPage(1);
        api.get(`${BASE}/${report}`, { params: asOf ? { as_of: asOf } : {} })
            .then(({ data: result }) => { if (active) setData(result); })
            .catch(() => { if (active) { setData(null); setError("تعذر تحميل تقرير ميزان 2. تحقق من الصلاحية والتاريخ ثم أعد المحاولة."); } })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [report, asOf, revision]);
    const ready = data?.status === "available";
    // New domain views require an explicit native V2 scope. The existing report contract is unchanged.
    const domainReady = !domain || data?.ledger_backend === "v2";
    const rows = useMemo(() => ready && domainReady ? data.items || [] : [], [ready, domainReady, data]);
    const domainRows = useMemo(() => rows.filter(row => !domain || row.entity_type === domain), [rows, domain]);
    const accounts = useMemo(() => Array.from(new Map(domainRows.map(row => [accountKey(row), { value: accountKey(row), label: `${row.entity_id} · ${LABELS[row.sub_account] || row.sub_account || row.entity_type}` }])).values()), [domainRows]);
    const sources = useMemo(() => [...new Set(domainRows.map(row => row.entry_type).filter(Boolean))].map(value => ({ value, label: value })), [domainRows]);
    const filtered = domainRows.filter(row => (!account || accountKey(row) === account) && (!source || row.entry_type === source) && textMatches(row, search));
    const visible = filtered.slice((page - 1) * 20, page * 20);
    const groupRows = selectedGroup ? rows.filter(row => row.txn_group_id === selectedGroup) : [];
    function resetFilters() { setSearch(""); setAccount(""); setSource(""); setPage(1); setSelectedGroup(null); }
    function selectReport(key) {
        if (report === key) return;
        setData(null); setReport(key); resetFilters();
        if (key === "financial-position") setDomain("");
    }
    return <section dir="rtl" className="ac-report space-y-5" aria-label="تقارير ميزان 2" data-testid="accounting-page-journals-reports">
        <AccountingPageHeader title="تقارير ميزان 2" description="القيود المعتمدة ضمن نطاق ميزان 2 بحسب التاريخ المحاسبي، بتوقيت الرياض.">
            <StatusBadge value="readonly" />
            <button type="button" disabled={!ready || !domainReady} onClick={() => window.print()}>طباعة / حفظ PDF</button>
        </AccountingPageHeader>
        {ready && report === "financial-position" && <FinancialSummaryCards items={["total_assets", "total_liabilities", "net_position"].map(key => ({ label: LABELS[key], value: data.totals?.[key], money: true, hint: "قيمة التقرير من الخادم" }))} />}
        <div className="ac-report-tabs" role="group" aria-label="اختيار التقرير">{REPORTS.map(([key, label]) =>
            <button type="button" key={key} aria-pressed={report === key} onClick={() => selectReport(key)}>{label}</button>)}</div>
        <form className="ac-report-date" onSubmit={event => { event.preventDefault(); setData(null); setAsOf(date); setRevision(value => value + 1); }}>
            <label>حتى نهاية اليوم<input type="date" aria-label="تاريخ التقرير" value={date} onChange={event => setDate(event.target.value)} /></label>
            <button type="submit">عرض التقرير</button>
            <button type="button" onClick={() => { setData(null); setDate(""); setAsOf(""); setRevision(value => value + 1); }}>التقرير الحالي</button>
        </form>
        {report !== "financial-position" && <AccountingFilters search={search} onSearch={value => { setSearch(value); setPage(1); }} onReset={resetFilters} scope="الفلاتر محلية على التقرير المحمّل (حتى 10,000 سطر قيد). الأرصدة والمدين والدائن من الخادم؛ ليست إجماليات لكل تاريخ المتجر.">
            <EntityPicker label="نوع التقرير" value={domain} onChange={value => { setDomain(value); resetFilters(); }} options={DOMAINS.filter(([key]) => key).map(([value, label]) => ({ value, label }))} />
            <EntityPicker value={account} onChange={value => { setAccount(value); setPage(1); }} options={accounts} />
            {report === "journals" && <EntityPicker label="المصدر" value={source} onChange={value => { setSource(value); setPage(1); }} options={sources} />}
        </AccountingFilters>}
        {domain && <div className="ac-report-notice"><StatusBadge value="BLOCKED_BY_BACKEND" /><p>{GAPS[domain]}</p></div>}
        {loading && <AccountingSkeleton label="جاري تحميل التقرير…" />}
        {error && <ErrorState message={error} onRetry={() => setRevision(value => value + 1)} />}
        {data && <div className="ac-report-notice" data-testid="accounting-report-readiness">
            <p>حالة التقرير: <StatusBadge value={data.status} /></p>
            {data.operation_id && <p>نطاق العملية: <bdi>{data.operation_id}</bdi></p>}
            <p>تاريخ التقرير: {asOf ? `${asOf} — نهاية اليوم بتوقيت الرياض` : "الحالي"}</p>
            {data.cutover_at && <p>تاريخ القطع: <bdi>{data.cutover_at}</bdi></p>}
            {data.opening_balance_txn_group_id && <p>قيد الرصيد الافتتاحي المعتمد: <bdi>{data.opening_balance_txn_group_id}</bdi></p>}
            {!ready && <><StatusBadge value="BLOCKED_BY_BACKEND" /><p>الأرصدة غير متاحة حتى استيفاء جاهزية القطع والتحقق من الرصيد الافتتاحي المعتمد. لا تمثل هذه الحالة رصيدًا صفريًا.</p>{typeof data.reason === "string" && <p>{data.reason}</p>}</>}
        </div>}
        {ready && !domainReady && <EmptyState title="كشف الحساب الموحد غير متاح" description="يتطلب هذا العرض مصدر دفتر ميزان 2 الأصلي. لا يُستخدم دفتر بديل لملء البيانات." />}
        {ready && report === "financial-position" && <>
            <div className="grid gap-4 md:grid-cols-2" data-testid="accounting-financial-position"><AmountList title="الأصول" values={data.assets} /><AmountList title="الالتزامات" values={data.liabilities} /></div>
        </>}
        {ready && domainReady && report !== "financial-position" && <>
            {visible.length === 0 ? <EmptyState title="لا توجد قيود في نطاق التقرير." description="لا تعني النتيجة الخالية وجود رصيد صفري. راجع تاريخ التقرير والفلاتر." /> : report === "journals" ?
                <JournalTable accountLabels={LABELS} rows={visible} onSelect={row => setSelectedGroup(row.txn_group_id || null)} /> :
                <div className="ac-table-scroll" tabIndex={0} role="region" aria-label="جدول ميزان المراجعة"><table className="ac-table" aria-label="ميزان مراجعة ميزان 2"><thead><tr>{["الحساب", "المعرف", "الحساب الفرعي", "مدين", "دائن", "الصافي كما ورد"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{visible.map((row, index) => <tr key={accountKey(row) + index}>
                    <td>{DOMAINS.find(([key]) => key === row.entity_type)?.[1] || row.entity_type}</td><td><bdi>{row.entity_id}</bdi></td><td>{LABELS[row.sub_account] || row.sub_account || "—"}</td>
                    <td><MoneyDisplay value={row.debits} /></td><td><MoneyDisplay value={row.credits} /></td><td><MoneyDisplay value={row.net} /></td>
                </tr>)}</tbody></table></div>}
            <AccountingPagination count={filtered.length} page={page} onChange={setPage} />
        </>}
        <AccountingDetails title={`تفاصيل القيد ${selectedGroup || ""}`} open={Boolean(selectedGroup)} onClose={() => setSelectedGroup(null)}>
            <p className="ac-report-notice">جميع أطراف المجموعة المحمّلة؛ لا يغيّر البحث أو اختيار حساب محتوى القيد.</p>
            <JournalTable accountLabels={LABELS} rows={groupRows} label="أطراف القيد" />
            {groupRows.map((row, index) => <div key={row.id || index} className="ac-report-notice mt-3"><p>المصدر: {row.entry_type || "غير متاح"}</p><p>التاريخ: <bdi>{journalDate(row) || "غير متاح"}</bdi></p><EvidenceBadge reference={row.metadata?.evidence_ref || row.metadata?.source_file_id} /></div>)}
        </AccountingDetails>
    </section>;
}
