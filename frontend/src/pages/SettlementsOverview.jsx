/**
 * SettlementsOverview — Iter-158
 *
 * Unified weekly settlements view across Salla + Tamara + Tabby with:
 *  - Month navigator (« / ») below the table
 *  - Columns: provider, invoice #, payment method, settlement date, net→bank
 *  - Per-row + select-all checkboxes
 *  - "📊 تصدير Excel" button that exports SELECTED rows
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const fmt = (n) => Number(n || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
});

const PROVIDER_META = {
    salla:  { label: "سلة 🟧",   color: "bg-orange-100 text-orange-800" },
    tabby:  { label: "Tabby 🟢", color: "bg-emerald-100 text-emerald-800" },
    tamara: { label: "Tamara 🟡", color: "bg-amber-100 text-amber-800" },
};

export default function SettlementsOverview() {
    const today = new Date();
    const [year, setYear] = useState(today.getFullYear());
    const [month, setMonth] = useState(today.getMonth() + 1);
    const [data, setData] = useState({ rows: [], provider_totals: {}, grand_total: {} });
    const [busy, setBusy] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [selected, setSelected] = useState(new Set());

    const load = async (y = year, m = month) => {
        setBusy(true);
        try {
            const { data } = await api.get(
                `/payment-settlements/_overview/unified?year=${y}&month=${m}`
            );
            setData(data);
            setSelected(new Set()); // clear selection on month change
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally {
            setBusy(false);
        }
    };

    useEffect(() => { load(year, month); /* eslint-disable-next-line */ }, [year, month]);

    const toggle = (id) => {
        const next = new Set(selected);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        setSelected(next);
    };
    const toggleAll = () => {
        if (selected.size === data.rows.length) setSelected(new Set());
        else setSelected(new Set(data.rows.map((r) => r.file_id)));
    };

    const navMonth = (delta) => {
        let m = month + delta;
        let y = year;
        if (m === 0) { m = 12; y -= 1; }
        if (m === 13) { m = 1; y += 1; }
        setMonth(m); setYear(y);
    };

    const exportSelected = async () => {
        if (selected.size === 0) {
            toast.error("اختر تسوية واحدة على الأقل");
            return;
        }
        setExporting(true);
        try {
            const r = await api.post(
                "/payment-settlements/_overview/export-excel",
                { file_ids: Array.from(selected) },
                { responseType: "blob" },
            );
            const url = URL.createObjectURL(new Blob([r.data]));
            const a = document.createElement("a");
            a.href = url;
            a.download = `settlements_${year}_${String(month).padStart(2, "0")}.xlsx`;
            a.click();
            URL.revokeObjectURL(url);
            toast.success(`تم تصدير ${selected.size} تسوية`);
        } catch (e) {
            toast.error("فشل التصدير");
        } finally {
            setExporting(false);
        }
    };

    const monthNames = ["يناير","فبراير","مارس","أبريل","مايو","يونيو","يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"];
    const t = data.grand_total || {};
    const allSelected = selected.size > 0 && selected.size === data.rows.length;

    return (
        <div dir="rtl" data-testid="settlements-overview-page" className="space-y-5 p-4 md:p-6 max-w-7xl mx-auto">
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-2xl font-extrabold text-slate-900">📑 جميع التسويات الموحَّدة</h1>
                    <p className="text-xs text-slate-500 mt-1">
                        فواتير التسوية الأسبوعية لـ سلة + Tamara + Tabby — مع التنقّل بين الأشهر وتصدير Excel للتسويات المحدَّدة.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={exportSelected} disabled={exporting || selected.size === 0}
                        className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 text-white text-sm font-bold rounded-lg"
                        data-testid="settlements-export-btn">
                        {exporting ? "جاري التصدير..." : `📊 تصدير ${selected.size > 0 ? `(${selected.size}) ` : ""}Excel`}
                    </button>
                    <Link to="/salla-settlements" className="text-xs text-slate-500 underline">← سلة</Link>
                    <Link to="/bnpl-settlements" className="text-xs text-slate-500 underline">← Tabby/Tamara</Link>
                </div>
            </div>

            {/* Totals */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Stat label="عدد التسويات" value={t.count || 0} testid="stat-count" />
                <Stat label="إجمالي المبيعات" value={`${fmt(t.gross || 0)} ر.س`} tone="slate" testid="stat-gross" />
                <Stat label="إجمالي العمولات" value={`${fmt(t.fees || 0)} ر.س`} tone="rose" testid="stat-fees" />
                <Stat label="الصافي للبنك" value={`${fmt(t.net || 0)} ر.س`} tone="emerald" bold testid="stat-net" />
            </div>

            {/* Table */}
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead className="bg-slate-50 text-slate-700">
                            <tr>
                                <th className="p-2 w-10">
                                    <input type="checkbox" checked={allSelected}
                                        onChange={toggleAll}
                                        data-testid="settlements-select-all"
                                        className="w-4 h-4 accent-emerald-600" />
                                </th>
                                <th className="p-2 text-right">المزوّد</th>
                                <th className="p-2 text-right">رقم الفاتورة</th>
                                <th className="p-2 text-right">طريقة الدفع</th>
                                <th className="p-2 text-right">تاريخ التحويل</th>
                                <th className="p-2 num text-right">إجمالي</th>
                                <th className="p-2 num text-right">عمولات</th>
                                <th className="p-2 num text-right">صافي للبنك</th>
                            </tr>
                        </thead>
                        <tbody>
                            {busy ? (
                                <tr><td colSpan={8} className="p-6 text-center text-slate-500">جاري التحميل...</td></tr>
                            ) : data.rows.length === 0 ? (
                                <tr><td colSpan={8} className="p-6 text-center text-slate-500">لا توجد تسويات في {monthNames[month - 1]} {year}.</td></tr>
                            ) : data.rows.map((r) => {
                                const meta = PROVIDER_META[r.provider] || { label: r.provider, color: "bg-slate-100 text-slate-700" };
                                return (
                                    <tr key={r.file_id} className="border-t border-slate-100 hover:bg-slate-50" data-testid={`settlement-row-${r.file_id}`}>
                                        <td className="p-2">
                                            <input type="checkbox" checked={selected.has(r.file_id)}
                                                onChange={() => toggle(r.file_id)}
                                                data-testid={`settlement-checkbox-${r.file_id}`}
                                                className="w-4 h-4 accent-emerald-600" />
                                        </td>
                                        <td className="p-2">
                                            <span className={`inline-flex px-2 py-1 rounded-full text-[11px] font-extrabold ${meta.color}`}>
                                                {meta.label}
                                            </span>
                                        </td>
                                        <td className="p-2 num font-bold">{r.invoice_number || "—"}</td>
                                        <td className="p-2 text-slate-700">{r.payment_method || "متعدد"}</td>
                                        <td className="p-2 text-slate-600" dir="ltr">{r.settlement_date || "—"}</td>
                                        <td className="p-2 num text-right">{fmt(r.gross)}</td>
                                        <td className="p-2 num text-right text-rose-700">{fmt(r.fees)}</td>
                                        <td className="p-2 num text-right font-extrabold text-emerald-700">{fmt(r.net_to_bank)}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>

                {/* Month navigation */}
                <div className="flex items-center justify-center gap-3 p-3 bg-slate-50 border-t border-slate-200">
                    <button onClick={() => navMonth(-1)} disabled={busy}
                        className="px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-100 rounded-lg text-xs font-bold disabled:opacity-40"
                        data-testid="month-prev">
                        ‹ الشهر السابق
                    </button>
                    <div className="text-sm font-extrabold text-slate-900 px-4" data-testid="current-month-label">
                        {monthNames[month - 1]} {year}
                    </div>
                    <button onClick={() => navMonth(1)} disabled={busy}
                        className="px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-100 rounded-lg text-xs font-bold disabled:opacity-40"
                        data-testid="month-next">
                        الشهر التالي ›
                    </button>
                </div>
            </div>
        </div>
    );
}

function Stat({ label, value, tone = "slate", bold = false, testid }) {
    const tones = {
        slate: "bg-slate-50 border-slate-200 text-slate-700",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-700",
        rose: "bg-rose-50 border-rose-200 text-rose-700",
    };
    return (
        <div className={`p-3 rounded-xl border ${tones[tone]}`} data-testid={testid}>
            <div className="text-[11px] opacity-80">{label}</div>
            <div className={`num mt-1 ${bold ? "text-lg font-extrabold" : "text-base font-bold"}`}>{value}</div>
        </div>
    );
}
