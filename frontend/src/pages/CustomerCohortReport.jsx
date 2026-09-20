import { useEffect, useState } from "react";
import {
    ArrowClockwise,
    CalendarBlank,
    ChartLineUp,
    CheckCircle,
    ShieldCheck,
    UsersThree,
    WarningCircle,
} from "@phosphor-icons/react";

import { getCustomerCohortReport } from "../services/customerCohortReport";

const money = (value) => Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

const riyadhToday = () => new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Riyadh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
}).format(new Date());

function MetricCard({ label, value, hint, tone = "slate", testid }) {
    const tones = {
        slate: "border-slate-200 bg-white text-slate-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        sky: "border-sky-200 bg-sky-50 text-sky-950",
    };
    return (
        <article className={`rounded-2xl border p-4 shadow-sm ${tones[tone]}`} data-testid={testid}>
            <div className="text-xs font-black opacity-70">{label}</div>
            <div className="mt-2 font-mono text-2xl font-black">{value}</div>
            {hint && <div className="mt-1 text-[11px] font-bold opacity-70">{hint}</div>}
        </article>
    );
}

function CoverageMoney({ title, value }) {
    return (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="text-xs font-black text-slate-600">{title}</div>
            <div className="mt-1 font-mono text-lg font-black text-slate-950">
                {money(value.known_amount_sar)} ر.س
            </div>
            <div className="mt-1 text-[10px] font-bold text-slate-500">
                اكتمال {value.coverage_pct}% · معلوم {value.known_orders} · غير معلوم {value.unknown_orders}
            </div>
        </div>
    );
}

export default function CustomerCohortReport() {
    const [asOf, setAsOf] = useState(riyadhToday);
    const [report, setReport] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");

    const load = async ({ silent = false } = {}) => {
        if (silent) setRefreshing(true);
        else setLoading(true);
        setError("");
        try {
            setReport(await getCustomerCohortReport(asOf));
        } catch (loadError) {
            const detail = loadError?.response?.data?.detail;
            setError(detail?.message || detail || "تعذّر تحميل تقرير العملاء.");
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    useEffect(() => {
        load();
        // Load again only when the selected report date changes.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [asOf]);

    if (loading) {
        return (
            <div className="flex min-h-[420px] items-center justify-center" data-testid="customer-cohort-loading">
                <ArrowClockwise size={28} className="animate-spin text-emerald-700" />
            </div>
        );
    }

    return (
        <main className="min-h-screen bg-slate-50 p-3 sm:p-5" dir="rtl" data-testid="customer-cohort-report-page">
            <div className="mx-auto max-w-7xl space-y-4">
                <header className="rounded-3xl bg-slate-950 p-5 text-white shadow-lg">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="flex flex-wrap items-center gap-2">
                                <h1 className="text-xl font-black sm:text-2xl">أفواج العملاء والشراء المتكرر</h1>
                                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-300 px-3 py-1 text-[10px] font-black text-emerald-950">
                                    <ShieldCheck size={14} weight="fill" /> قراءة فقط
                                </span>
                            </div>
                            <p className="mt-2 max-w-3xl text-xs font-bold leading-6 text-slate-300">
                                من طلبات سلة المطابقة ماليًا، مع فصل المصدر المؤكد عن غير المحسوم وعدم تحويل البيانات الناقصة إلى صفر.
                            </p>
                        </div>
                        <div className="flex flex-wrap items-end gap-2">
                            <label className="block">
                                <span className="mb-1 flex items-center gap-1 text-[11px] font-black text-slate-300"><CalendarBlank size={15} /> تاريخ التقرير</span>
                                <input
                                    type="date"
                                    value={asOf}
                                    max={riyadhToday()}
                                    onChange={(event) => setAsOf(event.target.value)}
                                    className="h-11 rounded-xl border border-white/20 bg-white px-3 font-mono text-sm font-black text-slate-950"
                                    data-testid="customer-cohort-as-of"
                                />
                            </label>
                            <button
                                type="button"
                                onClick={() => load({ silent: true })}
                                disabled={refreshing}
                                className="inline-flex h-11 items-center gap-2 rounded-xl bg-emerald-400 px-4 text-sm font-black text-emerald-950 disabled:opacity-60"
                                data-testid="customer-cohort-refresh"
                            >
                                <ArrowClockwise size={18} className={refreshing ? "animate-spin" : ""} />
                                تحديث
                            </button>
                        </div>
                    </div>
                </header>

                {error && (
                    <div className="flex items-center gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-800" data-testid="customer-cohort-error">
                        <WarningCircle size={20} /> {String(error)}
                    </div>
                )}

                {report && (
                    <>
                        {report.coverage.truncated === true && (
                            <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm font-black text-amber-900" data-testid="customer-cohort-truncated">
                                التقرير وصل إلى حد القراءة الآمن؛ الأرقام جزئية ولا تُستخدم لاتخاذ قرار ميزانية.
                            </div>
                        )}

                        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="ملخص العملاء">
                            <MetricCard label="العملاء المحددون" value={report.summary.identified_customers} hint="هوية سلة أو جوال/بريد قابل للمطابقة" testid="cohort-identified-customers" />
                            <MetricCard label="عملاء عادوا للشراء" value={report.summary.returning_customers_all_time} hint={`${report.summary.repeat_orders_all_time} طلبات لاحقة`} tone="emerald" testid="cohort-returning-customers" />
                            <MetricCard label="عائدون خلال 30 يومًا" value={report.returning.last_30_days.customers} hint={`${report.returning.last_30_days.orders} طلبات`} tone="sky" testid="cohort-returning-30" />
                            <MetricCard label="عائدون خلال 60 يومًا" value={report.returning.last_60_days.customers} hint={`${report.returning.last_60_days.orders} طلبات`} tone="amber" testid="cohort-returning-60" />
                        </section>

                        <section className="grid gap-3 lg:grid-cols-2">
                            <CoverageMoney title="تكلفة الاكتساب الأولى المؤكدة" value={report.summary.first_acquisition_cost} />
                            <CoverageMoney title="ربح مساهمة الطلبات اللاحقة" value={report.summary.later_order_contribution_profit} />
                        </section>

                        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid="customer-cohort-table">
                            <div className="flex items-center gap-2 border-b border-slate-100 p-4">
                                <UsersThree size={22} className="text-emerald-700" />
                                <div>
                                    <h2 className="font-black text-slate-950">تقسيم تاريخ أول شراء</h2>
                                    <p className="text-[11px] font-bold text-slate-500">المصدر والربح يظلان جزئيين حتى تكتمل بيانات الإسناد والتكاليف.</p>
                                </div>
                            </div>
                            <div className="overflow-x-auto">
                                <table className="w-full min-w-[980px] text-right text-sm">
                                    <thead className="bg-slate-50 text-xs text-slate-600">
                                        <tr>
                                            {["الفوج", "العملاء", "مبيعات أول طلب", "إعلاني مؤكد", "غير إعلاني صريح", "غير محسوم", "الطلبات اللاحقة", "تكلفة الاكتساب", "ربح الطلبات اللاحقة"].map((label) => (
                                                <th key={label} className="px-4 py-3 font-black">{label}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {report.cohorts.map((row) => (
                                            <tr key={row.key} data-testid={`customer-cohort-row-${row.key}`}>
                                                <td className="px-4 py-4 font-black text-slate-950">{row.label}</td>
                                                <td className="px-4 py-4 font-mono font-black">{row.customers}</td>
                                                <td className="px-4 py-4 font-mono">{money(row.first_order_sales_sar)} ر.س</td>
                                                <td className="px-4 py-4 font-mono text-emerald-700">{row.first_order_sources.confirmed_ad}</td>
                                                <td className="px-4 py-4 font-mono text-sky-700">{row.first_order_sources.explicit_non_ad}</td>
                                                <td className="px-4 py-4 font-mono text-amber-700">{row.first_order_sources.unresolved}</td>
                                                <td className="px-4 py-4 font-mono">{row.later_orders}</td>
                                                <td className="px-4 py-4"><div className="font-mono font-black">{money(row.first_acquisition_cost.known_amount_sar)} ر.س</div><div className="text-[10px] text-slate-500">اكتمال {row.first_acquisition_cost.coverage_pct}%</div></td>
                                                <td className="px-4 py-4"><div className="font-mono font-black">{money(row.later_order_contribution_profit.known_amount_sar)} ر.س</div><div className="text-[10px] text-slate-500">اكتمال {row.later_order_contribution_profit.coverage_pct}%</div></td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>

                        <section className="grid gap-3 lg:grid-cols-[1fr_1.2fr]">
                            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                <h2 className="flex items-center gap-2 font-black text-slate-950"><ChartLineUp size={20} /> جودة المصدر لأول طلب</h2>
                                <div className="mt-3 space-y-2 text-sm font-bold">
                                    <div className="flex justify-between rounded-xl bg-emerald-50 p-3 text-emerald-800"><span>إعلاني مؤكد</span><b>{report.summary.first_order_sources.confirmed_ad}</b></div>
                                    <div className="flex justify-between rounded-xl bg-sky-50 p-3 text-sky-800"><span>غير إعلاني صريح</span><b>{report.summary.first_order_sources.explicit_non_ad}</b></div>
                                    <div className="flex justify-between rounded-xl bg-amber-50 p-3 text-amber-800"><span>غير محسوم</span><b>{report.summary.first_order_sources.unresolved}</b></div>
                                </div>
                            </div>
                            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm font-bold text-emerald-950">
                                <h2 className="flex items-center gap-2 font-black"><CheckCircle size={20} weight="fill" /> قواعد القراءة</h2>
                                <ul className="mt-3 list-disc space-y-2 pr-5 leading-6">
                                    <li>لا تظهر أسماء العملاء أو الجوالات أو البريد في الاستجابة.</li>
                                    <li>تكلفة الاكتساب لا تُعتمد إلا لمصدر إعلاني مؤكد.</li>
                                    <li>ربح المساهمة = المبيعات − تكلفة المنتج − الصرف الإعلاني الموزع.</li>
                                    <li>المبلغ الناقص يبقى غير معلوم، ولا يدخل كصفر في الإجمالي.</li>
                                </ul>
                            </div>
                        </section>
                    </>
                )}
            </div>
        </main>
    );
}
