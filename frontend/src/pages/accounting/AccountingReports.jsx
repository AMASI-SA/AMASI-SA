import { useEffect, useState } from "react";
import api from "../../lib/api";
import { formatMoney } from "./AccountingShared";

const BASE = "/financial-provider-apps/accounting-module/reports";
const REPORTS = [
    ["financial-position", "المركز المالي"],
    ["trial-balance", "ميزان المراجعة"],
    ["journals", "القيود اليومية"],
];
const LABELS = {
    banks: "البنوك والصناديق", payment_platforms_remaining: "ذمم مزودي الدفع", inventory: "المخزون بالتكلفة", input_vat: "ضريبة مدخلات",
    customer_refund_payable: "التزام استرداد العميل", customer_advance: "تحصيلات العملاء المقدمة",
    sales_vat_payable: "ضريبة المبيعات المستحقة", employee_advance: "سلف الموظفين", employee_custody: "عهد الموظفين",
    external_receivable: "ذمم مدينة أخرى", courier_cod_receivable: "تحصيلات شركات الشحن", store_driver_cod_receivable: "تحصيلات موصلي المتجر",
    salaries_unpaid: "رواتب مستحقة", supplier_payable: "مستحقات الموردين", courier_payable: "مستحقات الشحن",
    store_driver_payable: "مستحقات الموصلين", external_payable: "ذمم دائنة أخرى", ad_accounts_unpaid: "مستحقات الإعلانات",
    total_assets: "إجمالي الأصول", total_liabilities: "إجمالي الالتزامات", net_position: "صافي المركز المالي",
};
const statusLabel = status => ({ available: "متاح", needs_opening_balance: "بانتظار رصيد افتتاحي معتمد", not_ready: "غير جاهز" }[status] || "غير جاهز");
function AmountList({ title, values }) {
    return <section className="rounded-xl border p-4"><h3 className="font-bold">{title}</h3>
        <dl>{Object.entries(values || {}).map(([key, value]) => <div key={key} className="flex justify-between gap-4 py-2">
            <dt>{LABELS[key] || key}</dt><dd dir="ltr">{formatMoney(value)}</dd>
        </div>)}</dl></section>;
}
export default function AccountingReports() {
    const [report, setReport] = useState("financial-position");
    const [date, setDate] = useState("");
    const [asOf, setAsOf] = useState("");
    const [revision, setRevision] = useState(0);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    useEffect(() => {
        let active = true;
        setLoading(true); setData(null); setError("");
        api.get(`${BASE}/${report}`, { params: asOf ? { as_of: asOf } : {} })
            .then(({ data: result }) => { if (active) setData(result); })
            .catch(() => { if (active) { setData(null); setError("تعذر تحميل تقرير ميزان 2. تحقق من الصلاحية والتاريخ ثم أعد المحاولة."); } })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [report, asOf, revision]);
    const ready = data?.status === "available";
    return <section dir="rtl" className="space-y-4" aria-label="تقارير ميزان 2" data-testid="accounting-page-journals-reports">
        <h2 className="text-xl font-bold">تقارير ميزان 2</h2>
        <p>القيود المعتمدة ضمن نطاق ميزان 2 بحسب التاريخ المحاسبي، بتوقيت الرياض.</p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="اختيار التقرير">{REPORTS.map(([key, label]) =>
            <button type="button" key={key} aria-pressed={report === key} className="rounded border px-3 py-2"
                onClick={() => { if (report !== key) { setData(null); setReport(key); } }}>{label}</button>)}</div>
        <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); setData(null); setAsOf(date); setRevision(value => value + 1); }}>
            <label>حتى نهاية اليوم<input type="date" aria-label="تاريخ التقرير" value={date} onChange={event => setDate(event.target.value)} className="block rounded border p-2" /></label>
            <button type="submit" className="rounded border px-3 py-2">عرض التقرير</button>
            <button type="button" className="rounded border px-3 py-2" onClick={() => { setData(null); setDate(""); setAsOf(""); setRevision(value => value + 1); }}>التقرير الحالي</button>
        </form>
        {loading && <p role="status">جاري تحميل التقرير…</p>}
        {error && <p role="alert" className="text-rose-800">{error}</p>}
        {data && <div className="rounded border p-3" data-testid="accounting-report-readiness">
            <p>حالة التقرير: {statusLabel(data.status)}</p>
            {data.operation_id && <p>نطاق العملية: <span dir="ltr">{data.operation_id}</span></p>}
            <p>تاريخ التقرير: {asOf ? `${asOf} — نهاية اليوم بتوقيت الرياض` : "الحالي"}</p>
            {data.cutover_at && <p>تاريخ القطع: <span dir="ltr">{data.cutover_at}</span></p>}
            {data.opening_balance_txn_group_id && <p>قيد الرصيد الافتتاحي المعتمد: <span dir="ltr">{data.opening_balance_txn_group_id}</span></p>}
            {!ready && <><p>الأرصدة غير متاحة حتى استيفاء جاهزية القطع والتحقق من الرصيد الافتتاحي المعتمد. لا تمثل هذه الحالة رصيدًا صفريًا.</p>
                {typeof data.reason === "string" && <p>{data.reason}</p>}</>}
        </div>}
        {ready && report === "financial-position" && <div className="grid gap-4 md:grid-cols-2" data-testid="accounting-financial-position">
            <AmountList title="الأصول" values={data.assets} /><AmountList title="الالتزامات" values={data.liabilities} />
            <AmountList title="الإجماليات" values={data.totals} />
        </div>}
        {ready && report === "trial-balance" && <div className="overflow-auto"><table className="w-full text-right" aria-label="ميزان مراجعة ميزان 2">
            <thead><tr>{["الحساب", "المعرف", "الحساب الفرعي", "مدين", "دائن", "الصافي"].map(label => <th key={label}>{label}</th>)}</tr></thead>
            <tbody>{(data.items || []).map((row, index) => <tr key={`${row.entity_type}:${row.entity_id}:${row.sub_account}:${index}`}>
                <td>{row.entity_type}</td><td>{row.entity_id}</td><td>{LABELS[row.sub_account] || row.sub_account || "—"}</td>
                <td>{formatMoney(row.debits)}</td><td>{formatMoney(row.credits)}</td><td>{formatMoney(row.net)}</td>
            </tr>)}</tbody></table>{!data.items?.length && <p>لا توجد قيود في نطاق التقرير.</p>}</div>}
        {ready && report === "journals" && <div className="overflow-auto"><table className="w-full text-right" aria-label="قيود ميزان 2">
            <thead><tr>{["التاريخ المحاسبي", "مجموعة القيد", "الحساب", "مدين", "دائن"].map(label => <th key={label}>{label}</th>)}</tr></thead>
            <tbody>{(data.items || []).map(row => <tr key={row.id}>
                <td dir="ltr">{row.metadata?.accounting_at || row.metadata?.recognized_at || "—"}</td><td dir="ltr">{row.txn_group_id}</td>
                <td>{LABELS[row.sub_account] || row.sub_account || row.entity_type} — {row.entity_id}</td>
                <td>{row.side === "debit" ? formatMoney(row.amount) : "—"}</td><td>{row.side === "credit" ? formatMoney(row.amount) : "—"}</td>
            </tr>)}</tbody></table>{!data.items?.length && <p>لا توجد قيود في نطاق التقرير.</p>}</div>}
    </section>;
}
