import { useState } from "react";
import {
    MagnifyingGlass, Warning, CheckCircle, ArrowsLeftRight, FileText, Copy, Download, X,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const inputCls =
    "w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition";

const fmt = (v) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtInt = (v) => Number(v || 0).toLocaleString("en-US");

function Stat({ label, value, hint, tone = "slate", testid }) {
    const tones = {
        slate:   "bg-slate-50 border-slate-200 text-slate-900",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
        rose:    "bg-rose-50 border-rose-200 text-rose-900",
        amber:   "bg-amber-50 border-amber-200 text-amber-900",
        sky:     "bg-sky-50 border-sky-200 text-sky-900",
    };
    return (
        <div className={`rounded-lg border p-3 ${tones[tone]}`} data-testid={testid}>
            <div className="text-[11px] font-bold opacity-80">{label}</div>
            <div className="num text-xl sm:text-2xl font-extrabold mt-0.5" style={{ fontFamily: "Tajawal" }}>
                {value}
            </div>
            {hint && <div className="text-[10px] text-muted-foreground mt-0.5">{hint}</div>}
        </div>
    );
}

function downloadCSV(rows, filename) {
    if (!rows.length) return;
    const headers = Object.keys(rows[0]);
    const lines = [headers.join(",")];
    for (const r of rows) {
        lines.push(headers.map((h) => {
            const v = r[h] ?? "";
            const s = String(v).replace(/"/g, '""');
            return /[",\n]/.test(s) ? `"${s}"` : s;
        }).join(","));
    }
    const blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
}

export default function OrdersDiagnostics() {
    const today = new Date().toISOString().slice(0, 10);
    const firstDayMonth = new Date(); firstDayMonth.setDate(1);
    const [fromDate, setFromDate] = useState(firstDayMonth.toISOString().slice(0, 10));
    const [toDate, setToDate] = useState(today);

    // Salla reference fields
    const [sallaOrders, setSallaOrders] = useState("");
    const [sallaSales, setSallaSales] = useState("");
    const [sallaNumbers, setSallaNumbers] = useState("");

    // Scan results
    const [summary, setSummary] = useState(null);
    const [scan, setScan] = useState(null);
    const [compare, setCompare] = useState(null);
    const [busy, setBusy] = useState(false);
    const [traceModal, setTraceModal] = useState(null);

    const runScan = async () => {
        setBusy(true);
        try {
            const params = { from_date: fromDate, to_date: toDate };
            const [sumRes, scanRes] = await Promise.all([
                api.get("/diagnostics/summary", { params }),
                api.get("/diagnostics/scan-duplicates", { params }),
            ]);
            setSummary(sumRes.data);
            setScan(scanRes.data);
            toast.success("تم التشخيص");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    const runCompare = async () => {
        if (!sallaOrders && !sallaSales && !sallaNumbers.trim()) {
            return toast.error("أدخل على الأقل: عدد طلبات سلة، إجمالي مبيعات سلة، أو قائمة أرقام طلبات.");
        }
        setBusy(true);
        try {
            const payload = { from_date: fromDate, to_date: toDate };
            if (sallaOrders !== "") payload.salla_orders_count = parseInt(sallaOrders, 10);
            if (sallaSales !== "")  payload.salla_total_sales  = parseFloat(sallaSales);
            if (sallaNumbers.trim()) {
                payload.salla_order_numbers = sallaNumbers
                    .split(/[,\n\r\s]+/).map((x) => x.trim()).filter(Boolean);
            }
            const { data } = await api.post("/diagnostics/compare-with-salla", payload);
            setCompare(data);
            toast.success("تمت المقارنة");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    const traceOrder = async (orderNumber) => {
        setBusy(true);
        try {
            const { data } = await api.get(`/diagnostics/order-trace/${encodeURIComponent(orderNumber)}`);
            setTraceModal(data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-6" data-testid="diagnostics-page">
            <header>
                <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                    تشخيص فروقات الطلبات
                </h1>
                <p className="text-muted-foreground">قارن أرقام النظام بأرقام سلة الفعلية، واكتشف الطلبات المكررة قبل أي تعديل.</p>
            </header>

            {/* Filters & Salla input */}
            <div className="bg-white rounded-xl border border-border p-4 grid grid-cols-1 md:grid-cols-2 gap-4" data-testid="diag-inputs">
                <div>
                    <h3 className="text-sm font-bold mb-3 flex items-center gap-1.5">
                        <FileText size={16} className="text-brand" /> الفترة الزمنية
                    </h3>
                    <div className="grid grid-cols-2 gap-2">
                        <div>
                            <label className="block text-xs font-bold text-muted-foreground mb-1">من تاريخ</label>
                            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className={inputCls} data-testid="diag-from-date" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-muted-foreground mb-1">إلى تاريخ</label>
                            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className={inputCls} data-testid="diag-to-date" />
                        </div>
                    </div>
                    <button onClick={runScan} disabled={busy} className="mt-3 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-brand text-white font-bold rounded-lg bg-brand-hover disabled:opacity-60" data-testid="diag-scan-btn">
                        <MagnifyingGlass size={18} weight="bold" /> {busy ? "جاري الفحص..." : "فحص التكرارات الآن"}
                    </button>
                </div>
                <div>
                    <h3 className="text-sm font-bold mb-3 flex items-center gap-1.5">
                        <ArrowsLeftRight size={16} className="text-brand" /> أرقام سلة المرجعية (من شاشة سلة الفعلية)
                    </h3>
                    <div className="grid grid-cols-2 gap-2">
                        <div>
                            <label className="block text-xs font-bold text-muted-foreground mb-1">عدد طلبات سلة</label>
                            <input type="number" value={sallaOrders} onChange={(e) => setSallaOrders(e.target.value)} className={inputCls} placeholder="مثال: 475" dir="ltr" style={{ textAlign: "right" }} data-testid="diag-salla-orders-input" />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-muted-foreground mb-1">إجمالي مبيعات سلة</label>
                            <input type="number" step="0.01" value={sallaSales} onChange={(e) => setSallaSales(e.target.value)} className={inputCls} placeholder="مثال: 94724.17" dir="ltr" style={{ textAlign: "right" }} data-testid="diag-salla-sales-input" />
                        </div>
                    </div>
                    <textarea value={sallaNumbers} onChange={(e) => setSallaNumbers(e.target.value)} className={`${inputCls} mt-2 min-h-[60px]`} placeholder="(اختياري) ألصق أرقام طلبات سلة هنا، مفصولة بفواصل أو سطور، لرؤية أي طلبات مفقودة." data-testid="diag-salla-numbers-input" />
                    <button onClick={runCompare} disabled={busy} className="mt-3 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-slate-800 text-white font-bold rounded-lg hover:bg-slate-700 disabled:opacity-60" data-testid="diag-compare-btn">
                        <ArrowsLeftRight size={18} weight="bold" /> مقارنة مع سلة
                    </button>
                </div>
            </div>

            {/* Summary stats */}
            {summary && (
                <div className="bg-white rounded-xl border border-border p-4" data-testid="diag-summary-card">
                    <h2 className="text-lg font-bold mb-3">ملخص المصادر للفترة المختارة</h2>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <Stat label="unified_orders" value={fmtInt(summary.unified.orders)} hint={`${fmt(summary.unified.sales)} ر.س`} tone="emerald" testid="stat-unified" />
                        <Stat label="legacy analyses" value={fmtInt(summary.legacy_analyses.orders)} hint={`${fmt(summary.legacy_analyses.sales)} ر.س — ${summary.legacy_analyses.count} ملف`} tone="amber" testid="stat-legacy" />
                        <Stat label="webhook_orders (إجمالي)" value={fmtInt(summary.webhook_orders_total)} hint="عبر كل الفترات" tone="sky" testid="stat-webhook" />
                        <Stat label="إجمالي النظام (الفترة)" value={fmtInt(summary.system_total.orders)} hint={`${fmt(summary.system_total.sales)} ر.س`} tone="slate" testid="stat-system-total" />
                    </div>
                </div>
            )}

            {/* Compare card */}
            {compare && (
                <div className="bg-white rounded-xl border border-border p-4" data-testid="diag-compare-card">
                    <h2 className="text-lg font-bold mb-3 flex items-center gap-2">
                        <ArrowsLeftRight size={18} className="text-brand" /> النظام vs سلة
                    </h2>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                        <Stat label="طلبات النظام" value={fmtInt(compare.system.total_orders)} tone="slate" />
                        <Stat label="طلبات سلة" value={compare.salla.orders_count != null ? fmtInt(compare.salla.orders_count) : "—"} tone="slate" />
                        {compare.diff && (
                            <Stat
                                label="الفرق في الطلبات"
                                value={compare.diff.orders > 0 ? `+${compare.diff.orders}` : compare.diff.orders}
                                hint={compare.diff.orders > 0 ? "النظام أكثر — يُحتمل تكرار" : (compare.diff.orders < 0 ? "النظام أقل — طلبات مفقودة" : "متطابق")}
                                tone={compare.diff.orders === 0 ? "emerald" : "rose"}
                                testid="stat-orders-diff"
                            />
                        )}
                        <Stat label="مبيعات النظام" value={`${fmt(compare.system.total_sales)} ر.س`} tone="slate" />
                        <Stat label="مبيعات سلة" value={compare.salla.total_sales != null ? `${fmt(compare.salla.total_sales)} ر.س` : "—"} tone="slate" />
                        {compare.diff && (
                            <Stat
                                label="الفرق في المبيعات"
                                value={`${compare.diff.sales >= 0 ? "+" : ""}${fmt(compare.diff.sales)} ر.س`}
                                hint={compare.diff.sales !== 0 ? `${Math.abs(compare.diff.sales).toFixed(2)} ر.س فرق` : "متطابق"}
                                tone={compare.diff.sales === 0 ? "emerald" : "rose"}
                                testid="stat-sales-diff"
                            />
                        )}
                    </div>

                    {compare.in_system_not_in_salla && (
                        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
                            <div className="rounded-lg border border-rose-200 bg-rose-50 p-3" data-testid="diag-in-system">
                                <div className="flex items-center justify-between mb-2">
                                    <div className="font-bold text-rose-900 text-sm">طلبات في النظام وليست في سلة ({compare.in_system_not_in_salla.count})</div>
                                    {compare.in_system_not_in_salla.samples.length > 0 && (
                                        <button onClick={() => downloadCSV(compare.in_system_not_in_salla.samples.map(n => ({ order_number: n })), "in-system-not-salla.csv")} className="text-xs px-2 py-1 bg-white border border-rose-300 rounded hover:bg-rose-100" data-testid="diag-export-in-system">
                                            <Download size={12} className="inline" /> CSV
                                        </button>
                                    )}
                                </div>
                                <div className="text-[11px] text-rose-800 max-h-40 overflow-y-auto font-mono leading-relaxed" dir="ltr" style={{ textAlign: "right" }}>
                                    {compare.in_system_not_in_salla.samples.length === 0 ? "لا يوجد" :
                                        compare.in_system_not_in_salla.samples.map((n) => (
                                            <button key={n} onClick={() => traceOrder(n)} className="inline-block px-1.5 py-0.5 mx-0.5 my-0.5 rounded bg-white hover:bg-rose-100 underline" title="تتبع هذا الطلب">{n}</button>
                                        ))
                                    }
                                </div>
                            </div>
                            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3" data-testid="diag-in-salla">
                                <div className="flex items-center justify-between mb-2">
                                    <div className="font-bold text-amber-900 text-sm">طلبات في سلة وليست في النظام ({compare.in_salla_not_in_system.count})</div>
                                    {compare.in_salla_not_in_system.samples.length > 0 && (
                                        <button onClick={() => downloadCSV(compare.in_salla_not_in_system.samples.map(n => ({ order_number: n })), "in-salla-not-system.csv")} className="text-xs px-2 py-1 bg-white border border-amber-300 rounded hover:bg-amber-100" data-testid="diag-export-in-salla">
                                            <Download size={12} className="inline" /> CSV
                                        </button>
                                    )}
                                </div>
                                <div className="text-[11px] text-amber-800 max-h-40 overflow-y-auto font-mono leading-relaxed" dir="ltr" style={{ textAlign: "right" }}>
                                    {compare.in_salla_not_in_system.samples.length === 0 ? "لا يوجد" :
                                        compare.in_salla_not_in_system.samples.map((n) => <span key={n} className="inline-block px-1.5 py-0.5 mx-0.5 my-0.5 rounded bg-white">{n}</span>)
                                    }
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Scan duplicates */}
            {scan && (
                <div className="space-y-4">
                    {/* Overlap (the smoking gun) */}
                    <div className={`bg-white rounded-xl border ${scan.legacy_overlap_summary.count > 0 ? "border-rose-300" : "border-emerald-200"} p-4`} data-testid="diag-overlap">
                        <div className="flex items-start gap-3">
                            {scan.legacy_overlap_summary.count > 0
                                ? <Warning size={28} className="text-rose-600 shrink-0" weight="duotone" />
                                : <CheckCircle size={28} className="text-emerald-600 shrink-0" weight="duotone" />
                            }
                            <div className="flex-1">
                                <h3 className="font-extrabold text-lg">
                                    {scan.legacy_overlap_summary.count > 0
                                        ? `${scan.legacy_overlap_summary.count} طلب يُحسب مرتين بسبب تداخل مع تحليلات قديمة`
                                        : "لا يوجد تداخل بين unified_orders والتحليلات القديمة"}
                                </h3>
                                <p className="text-sm text-muted-foreground mt-1">{scan.legacy_overlap_summary.explanation}</p>
                                {scan.legacy_overlap_summary.count > 0 && (
                                    <div className="mt-2 num text-2xl font-extrabold text-rose-700" style={{ fontFamily: "Tajawal" }} data-testid="diag-overlap-amount">
                                        تأثير على الإجمالي: +{fmt(scan.legacy_overlap_summary.total_amount)} ر.س مكرر
                                    </div>
                                )}
                            </div>
                        </div>
                        {scan.legacy_overlap_orders.length > 0 && (
                            <div className="mt-4 overflow-x-auto">
                                <table className="mezan-table compact w-full text-xs">
                                    <thead className="bg-slate-50 text-muted-foreground">
                                        <tr>
                                            <th className="text-right px-2 py-1.5 font-bold">رقم الطلب</th>
                                            <th className="text-right px-2 py-1.5 font-bold">المبلغ</th>
                                            <th className="text-right px-2 py-1.5 font-bold">تاريخ الطلب</th>
                                            <th className="text-right px-2 py-1.5 font-bold">طريقة الدفع</th>
                                            <th className="text-right px-2 py-1.5 font-bold">المصدر في unified</th>
                                            <th className="text-right px-2 py-1.5 font-bold">الملف القديم</th>
                                            <th className="text-right px-2 py-1.5 font-bold">تتبع</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {scan.legacy_overlap_orders.slice(0, 100).map((o, i) => (
                                            <tr key={i} className="border-t border-border">
                                                <td className="px-2 py-1.5 font-bold" dir="ltr" style={{ textAlign: "right" }}>{o.order_number}</td>
                                                <td className="px-2 py-1.5 num">{fmt(o.amount)}</td>
                                                <td className="px-2 py-1.5 text-muted-foreground">{o.order_date || "—"}</td>
                                                <td className="px-2 py-1.5 text-muted-foreground truncate">{o.payment_method || "—"}</td>
                                                <td className="px-2 py-1.5 text-muted-foreground">{o.unified_source || "—"}</td>
                                                <td className="px-2 py-1.5 text-muted-foreground truncate" title={`${o.legacy_filename} @ ${o.legacy_created}`}>{o.legacy_filename}</td>
                                                <td className="px-2 py-1.5">
                                                    <button onClick={() => traceOrder(o.order_number)} className="text-brand hover:underline">تتبع</button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                                <button onClick={() => downloadCSV(scan.legacy_overlap_orders, "legacy-overlap.csv")} className="mt-2 inline-flex items-center gap-1 text-xs text-brand font-bold hover:underline" data-testid="diag-export-overlap">
                                    <Download size={12} /> تصدير CSV كامل
                                </button>
                            </div>
                        )}
                    </div>

                    {/* Legacy file duplicates */}
                    {scan.legacy_file_duplicates.length > 0 && (
                        <div className="bg-white rounded-xl border border-amber-300 p-4" data-testid="diag-file-dups">
                            <div className="flex items-start gap-3 mb-3">
                                <Copy size={24} className="text-amber-700 shrink-0" weight="duotone" />
                                <div>
                                    <h3 className="font-extrabold text-lg">{scan.legacy_file_duplicates.length} ملف مرفوع أكثر من مرة</h3>
                                    <p className="text-sm text-muted-foreground">رفع نفس الملف يضيف أرقامه إلى الإجمالي مع كل رفعة.</p>
                                </div>
                            </div>
                            <div className="space-y-2">
                                {scan.legacy_file_duplicates.map((f) => (
                                    <div key={f.filename} className="border border-amber-200 rounded-lg p-3 bg-amber-50/40">
                                        <div className="font-bold text-sm mb-1">📄 {f.filename} — مرفوع {f.uploads.length} مرات</div>
                                        <table className="mezan-table compact w-full text-xs mt-1">
                                            <thead className="text-muted-foreground">
                                                <tr>
                                                    <th className="text-right px-2 py-1">التاريخ</th>
                                                    <th className="text-right px-2 py-1">عدد الطلبات</th>
                                                    <th className="text-right px-2 py-1">المبيعات</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {f.uploads.map((u) => (
                                                    <tr key={u.id} className="border-t border-amber-100">
                                                        <td className="px-2 py-1 text-muted-foreground">{u.created_at}</td>
                                                        <td className="px-2 py-1 num">{fmtInt(u.orders)}</td>
                                                        <td className="px-2 py-1 num">{fmt(u.sales)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Unified self-dups (should be 0) */}
                    {scan.unified_self_dups.length > 0 && (
                        <div className="bg-white rounded-xl border border-rose-300 p-4" data-testid="diag-self-dups">
                            <h3 className="font-extrabold text-rose-800">⚠️ تكرارات داخل unified_orders ({scan.unified_self_dups.length})</h3>
                            <p className="text-sm text-muted-foreground mb-2">هذا غير متوقع — يحتاج تدخل يدوي.</p>
                            <ul className="text-xs">
                                {scan.unified_self_dups.map((d) => (
                                    <li key={d.order_number} dir="ltr" style={{ textAlign: "right" }}>
                                        {d.order_number}: {d.count} نسخة
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}

            <div className="bg-sky-50/60 border border-sky-200 rounded-lg p-3 text-xs text-sky-900 flex items-start gap-2" data-testid="diag-info">
                <Warning size={18} className="text-sky-700 shrink-0 mt-0.5" weight="duotone" />
                <div>
                    هذه الأداة <strong>قراءة فقط</strong> — لا تحذف ولا تعدّل أي بيانات. هدفها كشف السبب الفعلي للفروقات قبل أي قرار. عند الاستعداد للدمج، يتم تنفيذه يدوياً بعد مراجعتك.
                </div>
            </div>

            {/* Trace modal */}
            {traceModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" data-testid="trace-modal">
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
                        <header className="flex items-center justify-between border-b border-border px-5 py-3">
                            <h3 className="font-bold text-lg">تتبع الطلب <span dir="ltr">{traceModal.order_number}</span></h3>
                            <button onClick={() => setTraceModal(null)} className="p-1.5 rounded hover:bg-accent" data-testid="trace-close-btn"><X size={20} /></button>
                        </header>
                        <div className="flex-1 overflow-y-auto px-5 py-4">
                            <p className="text-sm text-muted-foreground mb-3">
                                هذا الطلب موجود في <strong>{traceModal.found_in_stores_count}</strong> موقع داخل قاعدة البيانات:
                            </p>
                            {traceModal.locations.map((loc, i) => (
                                <div key={i} className="border border-border rounded-lg p-3 mb-2 bg-slate-50/40">
                                    <div className="font-bold text-sm text-brand">{loc.store}</div>
                                    <pre className="text-[11px] mt-1 whitespace-pre-wrap" dir="ltr">{JSON.stringify(loc, null, 2)}</pre>
                                </div>
                            ))}
                            {traceModal.locations.length === 0 && (
                                <div className="text-center text-muted-foreground py-6">لم يُعثر على هذا الطلب في أي مكان في قاعدة البيانات.</div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
