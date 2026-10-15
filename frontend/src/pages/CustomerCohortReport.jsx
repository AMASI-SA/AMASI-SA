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

const percent = (part, total) => {
    if (!total) return "0.0";
    return ((Number(part || 0) / Number(total)) * 100).toFixed(1);
};

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

function CoverageMoney({ title, value, unavailableReason }) {
    const hasKnownAmount = Number(value.known_orders || 0) > 0;
    return (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="text-xs font-black text-slate-600">{title}</div>
            <div className={`mt-1 text-lg font-black ${hasKnownAmount ? "font-mono text-slate-950" : "text-amber-800"}`}>
                {hasKnownAmount ? `${money(value.known_amount_sar)} ر.س` : "غير متوفر"}
            </div>
            <div className="mt-1 text-[10px] font-bold text-slate-500">
                {hasKnownAmount
                    ? `المبلغ المحسوب يغطي ${value.known_orders} طلبًا من أصل ${Number(value.known_orders || 0) + Number(value.unknown_orders || 0)}`
                    : unavailableReason}
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

    const identifiedCustomers = Number(report?.summary?.identified_customers || 0);
    const returningCustomers = Number(report?.summary?.returning_customers_all_time || 0);
    const repeatOrders = Number(report?.summary?.repeat_orders_all_time || 0);
    const recentCohort = report?.cohorts?.find((row) => row.key === "first_purchase_0_30_days");
    const priorCohort = report?.cohorts?.find((row) => row.key === "first_purchase_31_60_days");
    const customersOlderThan60 = Math.max(
        0,
        identifiedCustomers
            - Number(recentCohort?.customers || 0)
            - Number(priorCohort?.customers || 0),
    );
    const allSourcesUnresolved = identifiedCustomers > 0
        && Number(report?.summary?.first_order_sources?.unresolved || 0) === identifiedCustomers;

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
                                يوضح عدد العملاء الجدد، ومن عاد للشراء، ومتى حدث الشراء المتكرر — من طلبات سلة المطابقة ماليًا.
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

                        <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-950" data-testid="customer-cohort-plain-summary">
                            <h2 className="text-base font-black">الخلاصة ببساطة</h2>
                            <p className="mt-2 text-sm font-bold leading-7">
                                لدينا <b>{identifiedCustomers.toLocaleString("en-US")}</b> عميلًا معروفًا.
                                عاد منهم <b>{returningCustomers.toLocaleString("en-US")}</b> للشراء مرة أخرى
                                بنسبة <b>{percent(returningCustomers, identifiedCustomers)}%</b>،
                                وأنشؤوا <b>{repeatOrders.toLocaleString("en-US")}</b> طلبًا إضافيًا بعد أول طلب.
                            </p>
                            <p className="mt-1 text-xs font-bold text-emerald-800">
                                أرقام آخر 30 يومًا جزء من أرقام آخر 60 يومًا، لذلك لا تُجمع القيمتان معًا.
                            </p>
                        </section>

                        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="ملخص العملاء">
                            <MetricCard label="إجمالي العملاء المعروفين" value={identifiedCustomers} hint="لكل عميل هوية قابلة للمطابقة دون عرض بياناته" testid="cohort-identified-customers" />
                            <MetricCard label="اشتروا أكثر من مرة" value={returningCustomers} hint={`${percent(returningCustomers, identifiedCustomers)}% من إجمالي العملاء · ${repeatOrders} طلبًا إضافيًا`} tone="emerald" testid="cohort-returning-customers" />
                            <MetricCard label="لهم شراء متكرر في آخر 30 يومًا" value={report.returning.last_30_days.customers} hint={`${report.returning.last_30_days.orders} طلبات متكررة خلال الفترة`} tone="sky" testid="cohort-returning-30" />
                            <MetricCard label="لهم شراء متكرر في آخر 60 يومًا" value={report.returning.last_60_days.customers} hint={`${report.returning.last_60_days.orders} طلبات · يشمل آخر 30 يومًا`} tone="amber" testid="cohort-returning-60" />
                        </section>

                        <section className="grid gap-3 lg:grid-cols-2">
                            <CoverageMoney
                                title="تكلفة الحصول على العميل من الإعلان"
                                value={report.summary.first_acquisition_cost}
                                unavailableReason="لا يمكن حسابها حتى يُعرف أن أول طلب جاء من إعلان مؤكد وتتوفر تكلفته."
                            />
                            <CoverageMoney
                                title="ربح الطلبات بعد أول شراء"
                                value={report.summary.later_order_contribution_profit}
                                unavailableReason="لا يمكن حسابه حتى تكتمل تكلفة المنتج والصرف الإعلاني للطلبات المتكررة."
                            />
                        </section>

                        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid="customer-cohort-table">
                            <div className="flex items-center gap-2 border-b border-slate-100 p-4">
                                <UsersThree size={22} className="text-emerald-700" />
                                <div>
                                    <h2 className="font-black text-slate-950">العملاء الجدد حسب تاريخ أول شراء</h2>
                                    <p className="text-[11px] font-bold text-slate-500">
                                        الجدول يعرض من بدأوا الشراء خلال آخر 60 يومًا فقط. يوجد {customersOlderThan60.toLocaleString("en-US")} عميل أول شراء لهم أقدم من 60 يومًا.
                                    </p>
                                </div>
                            </div>
                            <div className="overflow-x-auto">
                                <table className="w-full min-w-[980px] text-right text-sm">
                                    <thead className="bg-slate-50 text-xs text-slate-600">
                                        <tr>
                                            {["فترة أول شراء", "العملاء", "مبيعات أول طلب", "من إعلان مؤكد", "مؤكد بدون إعلان", "المصدر غير معروف", "طلبات بعد الأول", "تكلفة الإعلان", "ربح الطلبات المتكررة"].map((label) => (
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
                                                <td className="px-4 py-4">
                                                    {row.first_acquisition_cost.known_orders > 0 ? (
                                                        <>
                                                            <div className="font-mono font-black">{money(row.first_acquisition_cost.known_amount_sar)} ر.س</div>
                                                            <div className="text-[10px] text-slate-500">محسوبة لـ{row.first_acquisition_cost.known_orders} طلب</div>
                                                        </>
                                                    ) : (
                                                        <>
                                                            <div className="font-black text-amber-800">غير متوفر</div>
                                                            <div className="text-[10px] text-slate-500">لا يوجد مصدر إعلاني مؤكد بتكلفة مكتملة</div>
                                                        </>
                                                    )}
                                                </td>
                                                <td className="px-4 py-4">
                                                    {row.later_order_contribution_profit.known_orders > 0 ? (
                                                        <>
                                                            <div className="font-mono font-black">{money(row.later_order_contribution_profit.known_amount_sar)} ر.س</div>
                                                            <div className="text-[10px] text-slate-500">محسوب لـ{row.later_order_contribution_profit.known_orders} طلب</div>
                                                        </>
                                                    ) : (
                                                        <>
                                                            <div className="font-black text-amber-800">غير متوفر</div>
                                                            <div className="text-[10px] text-slate-500">{row.later_orders ? "بيانات التكلفة أو الإعلان ناقصة" : "لا توجد طلبات بعد أول شراء"}</div>
                                                        </>
                                                    )}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>

                        <section className="grid gap-3 lg:grid-cols-[1fr_1.2fr]">
                            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                <h2 className="flex items-center gap-2 font-black text-slate-950"><ChartLineUp size={20} /> هل نعرف من أين جاء العميل أول مرة؟</h2>
                                {allSourcesUnresolved && (
                                    <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs font-black leading-6 text-amber-900" data-testid="customer-cohort-source-blocker">
                                        النتيجة الحالية: مصدر أول طلب غير معروف لجميع العملاء، لذلك لا يمكن ربطهم بالإعلانات أو حساب تكلفة اكتسابهم الآن.
                                    </div>
                                )}
                                <div className="mt-3 space-y-2 text-sm font-bold">
                                    <div className="flex justify-between rounded-xl bg-emerald-50 p-3 text-emerald-800"><span>جاء من إعلان مؤكد</span><b>{report.summary.first_order_sources.confirmed_ad}</b></div>
                                    <div className="flex justify-between rounded-xl bg-sky-50 p-3 text-sky-800"><span>مؤكد أنه لم يأتِ من إعلان</span><b>{report.summary.first_order_sources.explicit_non_ad}</b></div>
                                    <div className="flex justify-between rounded-xl bg-amber-50 p-3 text-amber-800"><span>مصدره غير معروف</span><b>{report.summary.first_order_sources.unresolved}</b></div>
                                </div>
                            </div>
                            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm font-bold text-emerald-950">
                                <h2 className="flex items-center gap-2 font-black"><CheckCircle size={20} weight="fill" /> لماذا تظهر بعض القيم «غير متوفرة»؟</h2>
                                <ul className="mt-3 list-disc space-y-2 pr-5 leading-6">
                                    <li>تكلفة الحصول على العميل تحتاج مصدر إعلان مؤكد وتكلفة الإعلان لأول طلب.</li>
                                    <li>ربح الطلبات المتكررة يحتاج المبيعات وتكلفة المنتج والصرف الإعلاني معًا.</li>
                                    <li>إذا نقص أحد هذه البيانات نعرض «غير متوفر» بدل رقم صفر مضلل.</li>
                                    <li>التقرير تجميعي ولا يعرض اسم العميل أو جواله أو بريده.</li>
                                </ul>
                            </div>
                        </section>
                    </>
                )}
            </div>
        </main>
    );
}
