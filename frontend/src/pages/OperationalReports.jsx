import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import { toast } from "sonner";

const fmt = (n) =>
    Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const PERIOD_LABELS = {
    daily: "تقرير يومي تفصيلي",
    monthly: "تقرير الشهر الحالي",
    yearly: "تقرير السنة الحالية",
};

export default function OperationalReports() {
    const nav = useNavigate();
    const [period, setPeriod] = useState("daily");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);

    const load = async (p) => {
        setLoading(true);
        try {
            const { data } = await api.get(`/operational-reports?period=${p}`);
            setData(data);
        } catch (e) {
            toast.error("فشل تحميل التقرير");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(period); /* eslint-disable-next-line */ }, [period]);

    if (loading || !data) {
        return <div className="p-8 text-center text-slate-500" data-testid="opreport-loading">جاري التحميل…</div>;
    }

    const c = data.categories;
    const s = data.summary;

    return (
        <>
            <style>{`
                @media print {
                    .no-print { display: none !important; }
                    body { font-size: 11px; }
                    .print-page { page-break-after: always; }
                }
                .num { font-family: 'Inter', system-ui; font-variant-numeric: tabular-nums; }
            `}</style>
            <div dir="rtl" className="p-4 sm:p-6 bg-slate-50 min-h-screen">
                {/* Header (hidden when printing) */}
                <div className="no-print flex items-center justify-between mb-4 flex-wrap gap-2">
                    <h1 className="text-2xl font-extrabold text-slate-900">التقارير التشغيلية</h1>
                    <div className="flex gap-2">
                        <button onClick={() => nav("/operating-expenses")} className="px-3 py-2 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm" data-testid="opreport-back">← رجوع</button>
                        <button onClick={() => window.print()} className="px-4 py-2 rounded-lg bg-emerald-700 text-white text-sm font-bold hover:bg-emerald-800" data-testid="opreport-print">🖨️ طباعة / حفظ PDF</button>
                    </div>
                </div>

                {/* Period selector */}
                <div className="no-print flex gap-2 mb-6">
                    {["daily", "monthly", "yearly"].map((p) => (
                        <button
                            key={p}
                            onClick={() => setPeriod(p)}
                            className={`px-4 py-2 rounded-lg text-sm font-bold transition ${period === p ? "bg-violet-700 text-white" : "bg-white text-slate-700 border border-slate-300"}`}
                            data-testid={`opreport-period-${p}`}
                        >
                            {PERIOD_LABELS[p]}
                        </button>
                    ))}
                </div>

                {/* Print area */}
                <div className="bg-white p-8 rounded-lg shadow-sm print-page" data-testid="opreport-content">
                    <div className="text-center mb-6 border-b-2 border-slate-200 pb-4">
                        <h2 className="text-2xl font-extrabold text-slate-900">{PERIOD_LABELS[period]}</h2>
                        <div className="text-sm text-slate-600 mt-1">
                            من <b className="num">{data.from_date}</b> إلى <b className="num">{data.to_date}</b>
                        </div>
                    </div>

                    {/* Yearly: per-month breakdown FIRST */}
                    {period === "yearly" && data.monthly_breakdown?.length > 0 && (
                        <div className="mb-8">
                            <h3 className="text-lg font-extrabold text-slate-800 mb-3 border-b border-slate-300 pb-2">📅 إجماليات شهرية</h3>
                            <table className="w-full text-xs border border-slate-300">
                                <thead className="bg-slate-100">
                                    <tr>
                                        <th className="border border-slate-300 p-2 text-right">الشهر</th>
                                        <th className="border border-slate-300 p-2 text-right">رواتب</th>
                                        <th className="border border-slate-300 p-2 text-right">إيجارات</th>
                                        <th className="border border-slate-300 p-2 text-right">يومية</th>
                                        <th className="border border-slate-300 p-2 text-right">مسبقة</th>
                                        <th className="border border-slate-300 p-2 text-right">شحن</th>
                                        <th className="border border-slate-300 p-2 text-right">عهد</th>
                                        <th className="border border-slate-300 p-2 text-right">فواتير</th>
                                        <th className="border border-slate-300 p-2 text-right">سداد</th>
                                        <th className="border border-slate-300 p-2 text-right font-extrabold">الإجمالي</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.monthly_breakdown.map((m) => (
                                        <tr key={m.month}>
                                            <td className="border border-slate-300 p-2 font-bold">{m.month_name}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.salaries)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.operating_rentals)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.operating_daily_expenses)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.operating_prepaid_expenses)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.operating_shipping)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.operating_advances)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.purchase_invoices)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(m.totals.liability_payments)}</td>
                                            <td className="border border-slate-300 p-2 num font-extrabold text-violet-900">{fmt(m.totals.grand_total)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                                <tfoot>
                                    <tr className="bg-violet-100">
                                        <td className="border border-slate-300 p-2 font-extrabold">إجمالي السنة</td>
                                        <td className="border border-slate-300 p-2 num font-bold" colSpan="9">
                                            {fmt(data.monthly_breakdown.reduce((sum, m) => sum + (m.totals.grand_total || 0), 0))} ر.س
                                        </td>
                                    </tr>
                                </tfoot>
                            </table>
                        </div>
                    )}

                    {/* Detailed line items per category */}
                    {Object.entries(c).map(([key, cat]) => {
                        if (!cat.items || cat.items.length === 0) return null;
                        return (
                            <div key={key} className="mb-6">
                                <h3 className="text-base font-extrabold text-slate-800 mb-2 bg-violet-50 p-2 rounded">
                                    {cat.label} <span className="text-xs font-normal text-slate-600">({cat.items.length} عملية)</span>
                                </h3>
                                <table className="w-full text-xs border border-slate-300">
                                    <thead className="bg-slate-50">
                                        <tr>
                                            <th className="border border-slate-300 p-2 text-right">التاريخ</th>
                                            <th className="border border-slate-300 p-2 text-right">الوصف</th>
                                            <th className="border border-slate-300 p-2 text-right">المبلغ</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {cat.items.slice(0, 200).map((it, i) => (
                                            <tr key={i}>
                                                <td className="border border-slate-300 p-2 num">{it.date || it.rental_date || it.expense_date || it.invoice_date || it.payment_date || it.due_date || it.advance_date || "—"}</td>
                                                <td className="border border-slate-300 p-2">{it.description || it.label || it.name || it.notes || it.ad_account_label || "—"}</td>
                                                <td className="border border-slate-300 p-2 num font-bold">{fmt(it.amount || it.expected_amount || it.total_amount || 0)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                    <tfoot>
                                        <tr className="bg-slate-100">
                                            <td className="border border-slate-300 p-2 font-bold" colSpan="2">إجمالي {cat.label}</td>
                                            <td className="border border-slate-300 p-2 num font-extrabold text-violet-900">{fmt(cat.total || cat.total_expected || 0)}</td>
                                        </tr>
                                    </tfoot>
                                </table>
                            </div>
                        );
                    })}

                    {/* Employees summary */}
                    {data.employees && data.employees.length > 0 && (
                        <div className="mb-6">
                            <h3 className="text-base font-extrabold text-slate-800 mb-2 bg-amber-50 p-2 rounded">👥 ملخص الموظفين</h3>
                            <table className="w-full text-xs border border-slate-300">
                                <thead className="bg-slate-50">
                                    <tr>
                                        <th className="border border-slate-300 p-2 text-right">الموظف</th>
                                        <th className="border border-slate-300 p-2 text-right">أيام العمل</th>
                                        <th className="border border-slate-300 p-2 text-right">الراتب المستحق</th>
                                        <th className="border border-slate-300 p-2 text-right">المدفوع</th>
                                        <th className="border border-slate-300 p-2 text-right">متبقي للموظف</th>
                                        <th className="border border-slate-300 p-2 text-right">مديونية عليه</th>
                                        <th className="border border-slate-300 p-2 text-right">الصافي</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.employees.map((e) => (
                                        <tr key={e.employee_id}>
                                            <td className="border border-slate-300 p-2 font-bold">{e.name || e.employee_id}</td>
                                            <td className="border border-slate-300 p-2 num">{e.days_worked}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(e.salary_due)}</td>
                                            <td className="border border-slate-300 p-2 num">{fmt(e.paid)}</td>
                                            <td className="border border-slate-300 p-2 num text-rose-700">{fmt(e.remaining)}</td>
                                            <td className="border border-slate-300 p-2 num text-emerald-700">{fmt(e.advance)}</td>
                                            <td className={`border border-slate-300 p-2 num font-extrabold ${e.net > 0 ? "text-rose-700" : "text-emerald-700"}`}>
                                                {e.net > 0 ? "+" : ""}{fmt(e.net)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {/* Final summary box */}
                    <div className="mt-8 p-5 bg-slate-100 rounded-lg border-2 border-slate-300">
                        <h3 className="text-lg font-extrabold text-slate-900 mb-3 text-center">📊 الملخص العام</h3>
                        <div className="grid grid-cols-2 gap-3 text-sm">
                            <div className="bg-white p-3 rounded border border-slate-200">
                                <div className="text-slate-600">إجمالي المصروفات التشغيلية</div>
                                <div className="num font-extrabold text-violet-900 text-lg">{fmt(s.gross_expense)} ر.س</div>
                            </div>
                            <div className="bg-white p-3 rounded border border-slate-200">
                                <div className="text-slate-600">إجمالي المدفوع</div>
                                <div className="num font-extrabold text-emerald-700 text-lg">{fmt(s.gross_paid)} ر.س</div>
                            </div>
                            <div className="bg-white p-3 rounded border border-slate-200">
                                <div className="text-slate-600">إجمالي غير المدفوع</div>
                                <div className="num font-extrabold text-rose-700 text-lg">{fmt(s.unpaid)} ر.س</div>
                            </div>
                            <div className="bg-white p-3 rounded border border-slate-200">
                                <div className="text-slate-600">الالتزامات المفتوحة</div>
                                <div className="num font-extrabold text-amber-700 text-lg">{fmt(s.open_liabilities_total)} ر.س</div>
                            </div>
                            <div className="bg-white p-3 rounded border border-slate-200">
                                <div className="text-slate-600">مستحقات الموظفين</div>
                                <div className="num font-extrabold text-rose-700 text-lg">{fmt(s.employees_we_owe)} ر.س</div>
                            </div>
                            <div className="bg-white p-3 rounded border border-slate-200">
                                <div className="text-slate-600">مديونيات على الموظفين</div>
                                <div className="num font-extrabold text-emerald-700 text-lg">{fmt(s.employees_owed_to_us)} ر.س</div>
                            </div>
                            <div className="bg-rose-50 p-3 rounded border-2 border-rose-300 col-span-2">
                                <div className="text-rose-900 font-bold">💰 صافي المبالغ المطلوبة منّي</div>
                                <div className="num font-extrabold text-rose-900 text-2xl">{fmt(s.net_we_owe)} ر.س</div>
                            </div>
                            {s.net_owed_to_us > 0 && (
                                <div className="bg-emerald-50 p-3 rounded border-2 border-emerald-300 col-span-2">
                                    <div className="text-emerald-900 font-bold">💰 صافي المبالغ المطلوبة لي</div>
                                    <div className="num font-extrabold text-emerald-900 text-2xl">{fmt(s.net_owed_to_us)} ر.س</div>
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="text-center text-[10px] text-slate-500 mt-8 pt-4 border-t border-slate-200">
                        تم إنشاء التقرير في {new Date().toLocaleString("ar-SA")} · MEZAN
                    </div>
                </div>
            </div>
        </>
    );
}
