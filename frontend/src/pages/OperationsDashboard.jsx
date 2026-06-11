/**
 * لوحة العمليات (Operations Dashboard) — Iter-104
 *
 * Lightweight aggregator over EXISTING endpoints:
 *   • /api/purchase-invoices                    → invoiced + paid totals
 *   • /api/liabilities?kind=supplier            → suppliers liability
 *   • /api/liabilities?kind=salary_advance      → advances outstanding
 *   • /api/liabilities?kind=receivable          → receivables
 * No new collections. Pure read-only synthesis.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
    Briefcase, Receipt, HandCoins, Truck, Users, ArrowRight,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { SalaryAccrualSummaryCard } from "../components/EmployeeBalanceCard";


const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });


function KPI({ title, value, sub, tone = "slate", Icon, link, linkLabel, testid }) {
    const tones = {
        slate:   "bg-white border-slate-200",
        violet:  "bg-violet-50 border-violet-200",
        rose:    "bg-rose-50 border-rose-200",
        emerald: "bg-emerald-50 border-emerald-200",
        amber:   "bg-amber-50 border-amber-200",
    };
    return (
        <div className={`rounded-xl border p-4 ${tones[tone]}`} data-testid={testid}>
            <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-bold text-slate-700">{title}</div>
                {Icon && <Icon size={20} weight="duotone" className="text-slate-500" />}
            </div>
            <div className="num text-2xl font-extrabold text-slate-900">{fmt(value)} ر.س</div>
            {sub && <div className="text-[11px] text-slate-500 mt-1">{sub}</div>}
            {link && (
                <Link to={link} className="inline-flex items-center gap-1 mt-2 text-[11px] font-bold text-violet-700 hover:underline">
                    {linkLabel || "عرض"} <ArrowRight size={11} />
                </Link>
            )}
        </div>
    );
}


export default function OperationsDashboard() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const [pinv, supp, adv, recv] = await Promise.all([
                api.get("/purchase-invoices?limit=2000"),
                api.get("/liabilities?kind=supplier&limit=2000"),
                api.get("/liabilities?kind=salary_advance&limit=2000"),
                api.get("/liabilities?kind=receivable&limit=2000"),
            ]);
            setData({
                invoices: pinv.data?.items || [],
                suppliers: supp.data?.items || [],
                advances: adv.data?.items || [],
                receivables: recv.data?.items || [],
            });
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    const k = useMemo(() => {
        if (!data) return null;
        const sum = (arr, fld) => arr.reduce((acc, x) => acc + Number(x[fld] || 0), 0);
        const inv_total      = sum(data.invoices, "total");
        const inv_paid       = sum(data.invoices, "paid_amount");
        const inv_remaining  = sum(data.invoices, "remaining_amount");

        const supp_remaining = data.suppliers
            .filter((l) => l.status !== "paid")
            .reduce((acc, l) => acc + Number(l.remaining_amount || 0), 0);

        // Advances: positive remaining = employee still owes us.
        const adv_outstanding = data.advances.reduce(
            (acc, l) => acc + Math.max(0, Number(l.remaining_amount || 0)),
            0,
        );
        const adv_count = data.advances.filter((l) => Number(l.remaining_amount || 0) > 0).length;

        const recv_invoiced  = sum(data.receivables, "expected_amount");
        const recv_collected = sum(data.receivables, "paid_amount");
        const recv_remaining = sum(data.receivables, "remaining_amount");
        const recv_overdue   = data.receivables.filter((l) => l.is_overdue && l.status !== "paid").length;

        return {
            inv_total, inv_paid, inv_remaining,
            supp_remaining,
            adv_outstanding, adv_count,
            recv_invoiced, recv_collected, recv_remaining, recv_overdue,
        };
    }, [data]);

    if (loading || !k) {
        return (
            <div dir="rtl" className="p-10 text-center text-slate-500" data-testid="ops-loading">
                جاري التحميل…
            </div>
        );
    }

    return (
        <div dir="rtl" data-testid="operations-dashboard-page" className="space-y-6">
            <div>
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                    <Briefcase size={28} weight="duotone" className="text-violet-700" />
                    لوحة العمليات
                </h1>
                <p className="text-sm text-slate-500 mt-1">
                    نظرة شاملة على المشتريات، الموردين، العهد، والذمم المدينة.
                    كل الأرقام مصدرها مباشر من سجلاتك في النظام — لا حسابات مكررة.
                </p>
            </div>

            {/* Purchase Invoices */}
            <section>
                <h2 className="text-base font-bold text-slate-800 mb-3 flex items-center gap-2">
                    <Receipt size={18} weight="duotone" className="text-slate-500" />
                    فواتير المشتريات
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <KPI title="إجمالي الفواتير" value={k.inv_total} sub={`${data.invoices.length} فاتورة`}
                        Icon={Receipt} testid="ops-kpi-inv-total" link="/purchase-invoices" />
                    <KPI title="المسدَّد" value={k.inv_paid} tone="emerald"
                        sub="من الموردين" testid="ops-kpi-inv-paid" />
                    <KPI title="المتبقي للموردين" value={k.inv_remaining} tone="rose"
                        sub={`موردون يستحقون السداد: ${data.suppliers.filter((s) => s.status !== "paid").length}`}
                        testid="ops-kpi-inv-remaining" link="/purchase-invoices" linkLabel="عرض الفواتير غير المسدَّدة" />
                </div>
            </section>

            {/* Advances */}
            <section>
                <h2 className="text-base font-bold text-slate-800 mb-3 flex items-center gap-2">
                    <HandCoins size={18} weight="duotone" className="text-slate-500" />
                    عهد وسلف الموظفين والمندوبين
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <KPI title="السلف المفتوحة" value={k.adv_outstanding} tone="violet"
                        sub={`${k.adv_count} سلفة لم تُسترَدّ بعد`}
                        Icon={Users}
                        testid="ops-kpi-adv-outstanding"
                        link="/advances" />
                    <KPI title="إجمالي السلف المُسجَّلة" value={data.advances.reduce((a, l) => a + Number(l.expected_amount || 0), 0)}
                        sub={`${data.advances.length} سجل (يشمل المُسوَّى)`}
                        testid="ops-kpi-adv-total" />
                    <KPI title="السلف المُسوَّاة" value={data.advances.reduce((a, l) => a + Number(l.paid_amount || 0), 0)}
                        tone="emerald" sub="من خصم رواتب أو سداد نقدي"
                        testid="ops-kpi-adv-recovered" />
                </div>
            </section>

            {/* Receivables */}
            <section>
                <h2 className="text-base font-bold text-slate-800 mb-3 flex items-center gap-2">
                    <Truck size={18} weight="duotone" className="text-slate-500" />
                    الذمم المدينة والتحصيلات
                </h2>                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                    <KPI title="إجمالي الذمم" value={k.recv_invoiced}
                        sub={`${data.receivables.length} ذمة`}
                        Icon={Truck}
                        testid="ops-kpi-recv-total"
                        link="/receivables" />
                    <KPI title="المحصَّل" value={k.recv_collected} tone="emerald"
                        sub="دخل للبنوك فعلياً" testid="ops-kpi-recv-collected" />
                    <KPI title="المتبقي للتحصيل" value={k.recv_remaining} tone="rose"
                        sub={k.recv_overdue ? `⚠️ ${k.recv_overdue} ذمة متأخرة` : "لا توجد ذمم متأخرة"}
                        testid="ops-kpi-recv-remaining"
                        link="/receivables" linkLabel="عرض الذمم المفتوحة" />
                    <KPI title="نسبة التحصيل" value={k.recv_invoiced > 0 ? Number((k.recv_collected / k.recv_invoiced) * 100).toFixed(2) : 0}
                        tone="violet" sub="%" testid="ops-kpi-recv-rate" />
                </div>
            </section>

            {/* Iter-138 — Salary accrual (unified source of truth) */}
            <section data-testid="ops-salary-accrual-section">
                <h2 className="text-base font-bold text-slate-800 mb-3 flex items-center gap-2">
                    <Users size={18} weight="duotone" className="text-slate-500" />
                    تراكم رواتب الموظفين
                    <span className="text-[10px] font-normal text-slate-500">
                        — نفس البيانات الظاهرة في «المركز المالي» و«التقارير التشغيلية» (مصدر موحَّد).
                    </span>
                </h2>
                <SalaryAccrualSummaryCard showEmployeeTable={true} />
            </section>
        </div>
    );
}
