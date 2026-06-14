/**
 * COD Source Diagnostic — Iter-176
 *
 * Transparent breakdown of how the "الدفع عند الاستلام" account
 * balance was computed BEFORE the merchant executes Phase 4
 * Closeout. Reads from `GET /api/diagnostics/cod-source`.
 *
 * Highlights:
 *  • Confirmed vs Delivered distinction (system currently uses
 *    Confirmed per order_status_policy).
 *  • Per shipping company breakdown (SMSA, iMile, مندوب الرياض, …).
 *  • Reconciliation math: expected + IN − OUT = current_balance.
 */
import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import api from "../lib/api";
import { PaymentModeBadge } from "../components/PaymentModeBadge";

const errMsg = (e, fb) =>
    e?.response?.data?.error || e?.response?.data?.detail || e?.message || fb;

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const intf = (n) => (Number(n) || 0).toLocaleString("en-US");

const CATEGORY_META = {
    confirmed: {
        label: "Confirmed (مُحتسبة في الرصيد)",
        tone: "emerald",
        included: true,
    },
    pending:   { label: "Pending (معلَّقة)",   tone: "amber",  included: false },
    cancelled: { label: "Cancelled (ملغاة)",   tone: "rose",   included: false },
    refunded:  { label: "Refunded (مُسترجعة)", tone: "slate",  included: false },
    _unknown:  { label: "غير مُصنَّفة",         tone: "slate",  included: false },
};

const TONE_BG = {
    emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
    amber:   "bg-amber-50 border-amber-200 text-amber-900",
    rose:    "bg-rose-50 border-rose-200 text-rose-900",
    slate:   "bg-slate-50 border-slate-200 text-slate-700",
};

function StatCard({ label, value, sublabel, tone = "slate", testid }) {
    const cls = TONE_BG[tone] || TONE_BG.slate;
    return (
        <div
            className={`rounded-2xl border-2 ${cls} p-4 shadow-sm`}
            data-testid={testid}
        >
            <div className="text-xs font-semibold opacity-80 mb-1">{label}</div>
            <div className="num text-2xl font-extrabold">{value}</div>
            {sublabel && (
                <div className="text-[11px] opacity-70 mt-1">{sublabel}</div>
            )}
        </div>
    );
}

function CategoryTable({ rows }) {
    return (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm" data-testid="cod-category-table">
                <thead className="bg-slate-50 text-slate-700">
                    <tr>
                        <th className="px-3 py-2 text-right font-bold">الفئة</th>
                        <th className="px-3 py-2 text-left font-bold">عدد الطلبات</th>
                        <th className="px-3 py-2 text-left font-bold">إجمالي المبلغ</th>
                        <th className="px-3 py-2 text-center font-bold">يدخل في الرصيد؟</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r) => {
                        const meta = CATEGORY_META[r.key] || CATEGORY_META._unknown;
                        return (
                            <tr key={r.key} className="border-t border-slate-100">
                                <td className="px-3 py-2 text-right">{meta.label}</td>
                                <td className="px-3 py-2 num text-left">{intf(r.count)}</td>
                                <td className="px-3 py-2 num text-left font-semibold">{fmt(r.gross)}</td>
                                <td className="px-3 py-2 text-center">
                                    {meta.included ? (
                                        <span className="inline-block px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 text-xs font-bold">
                                            نعم ✓
                                        </span>
                                    ) : (
                                        <span className="inline-block px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 text-xs font-bold">
                                            لا ✗
                                        </span>
                                    )}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

function CompanyTable({ rows, testid, emphasize = false, paymentModeMap = {} }) {
    if (!rows?.length) {
        return (
            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-center text-slate-500 text-sm" data-testid={`${testid}-empty`}>
                لا توجد بيانات
            </div>
        );
    }
    return (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm" data-testid={testid}>
                <thead className={`${emphasize ? "bg-emerald-50 text-emerald-900" : "bg-slate-50 text-slate-700"}`}>
                    <tr>
                        <th className="px-3 py-2 text-right font-bold">شركة الشحن</th>
                        <th className="px-3 py-2 text-center font-bold">طريقة السداد</th>
                        <th className="px-3 py-2 text-left font-bold">عدد الطلبات</th>
                        <th className="px-3 py-2 text-left font-bold">إجمالي COD</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r) => {
                        const mode = paymentModeMap[(r.company || "").trim()];
                        return (
                            <tr key={r.company} className="border-t border-slate-100">
                                <td className="px-3 py-2 text-right font-semibold">{r.company}</td>
                                <td className="px-3 py-2 text-center">
                                    {mode != null
                                        ? <PaymentModeBadge payment_mode={mode} size="xs" />
                                        : <span className="text-[10px] text-slate-400">—</span>}
                                </td>
                                <td className="px-3 py-2 num text-left">{intf(r.count)}</td>
                                <td className="px-3 py-2 num text-left font-bold">{fmt(r.gross)}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

function RawStatusTable({ rows }) {
    if (!rows?.length) return null;
    return (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm" data-testid="cod-raw-status-table">
                <thead className="bg-slate-50 text-slate-700">
                    <tr>
                        <th className="px-3 py-2 text-right font-bold">الحالة الخام</th>
                        <th className="px-3 py-2 text-left font-bold">العدد</th>
                        <th className="px-3 py-2 text-left font-bold">الإجمالي</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r) => (
                        <tr key={r.status} className="border-t border-slate-100">
                            <td className="px-3 py-2 text-right">{r.status || "—"}</td>
                            <td className="px-3 py-2 num text-left">{intf(r.count)}</td>
                            <td className="px-3 py-2 num text-left">{fmt(r.gross)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function PostSyncDriftBlock({ drift }) {
    const amtDiff = drift.cache_vs_walk_amount_diff || 0;
    const countDiff = drift.cache_vs_walk_count_diff || 0;
    const orders = drift.orders || [];
    const healthy = Math.abs(amtDiff) < 0.5 && countDiff === 0;
    const lastSync = drift.last_synced_at
        ? new Date(drift.last_synced_at).toLocaleString("en-GB", {
            timeZone: "Asia/Riyadh",
            hour12: false,
        })
        : "—";

    return (
        <div className={`rounded-2xl shadow p-5 mb-6 border-2 ${healthy ? "bg-emerald-50 border-emerald-300" : "bg-amber-50 border-amber-300"}`}
             data-testid="cod-post-sync-drift">
            <h3 className="font-extrabold text-slate-900 mb-3">
                ⏱️ تحليل الفرق بين الـ Cache و الـ Walk
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                <div className="rounded-lg bg-white border border-slate-200 p-3">
                    <div className="text-[11px] text-slate-500 font-bold mb-1">آخر مزامنة للحساب</div>
                    <div className="text-xs font-extrabold text-slate-900">{lastSync}</div>
                </div>
                <div className="rounded-lg bg-white border border-slate-200 p-3">
                    <div className="text-[11px] text-slate-500 font-bold mb-1">عدد الطلبات (Walk / Cache)</div>
                    <div className="text-base num font-extrabold text-slate-900">
                        {intf(drift.walk_total_orders_count)} / {intf(drift.cache_orders_count)}
                    </div>
                    <div className={`text-[11px] mt-0.5 font-bold ${countDiff === 0 ? "text-emerald-700" : "text-amber-700"}`}>
                        {countDiff > 0 ? "+" : ""}{intf(countDiff)} طلب
                    </div>
                </div>
                <div className="rounded-lg bg-white border border-slate-200 p-3">
                    <div className="text-[11px] text-slate-500 font-bold mb-1">المبلغ (Walk Confirmed / Cache)</div>
                    <div className="text-xs num font-extrabold text-slate-900">
                        {fmt(drift.walk_confirmed_total)} / {fmt(drift.cache_current_balance)}
                    </div>
                    <div className={`text-[11px] mt-0.5 font-bold ${Math.abs(amtDiff) < 0.5 ? "text-emerald-700" : "text-amber-700"}`}>
                        فرق: {fmt(amtDiff)}
                    </div>
                </div>
                <div className="rounded-lg bg-white border border-slate-200 p-3">
                    <div className="text-[11px] text-slate-500 font-bold mb-1">طلبات بعد المزامنة</div>
                    <div className="text-xl num font-extrabold text-slate-900">{intf(drift.post_sync_orders_count)}</div>
                    <div className="text-[11px] text-slate-500 mt-0.5">
                        مجموعها {fmt(drift.post_sync_total_confirmed)} ر.س (Confirmed)
                    </div>
                </div>
            </div>

            <div className="text-sm leading-relaxed text-slate-800 bg-white/70 rounded-lg p-3 border border-slate-200 mb-3">
                <strong className="text-slate-900">التفسير: </strong>
                {drift.interpretation}
            </div>

            {orders.length > 0 && (
                <details className="bg-white rounded-lg border border-slate-200">
                    <summary className="cursor-pointer p-3 font-extrabold text-slate-900 select-none text-sm">
                        📋 قائمة الطلبات ({intf(orders.length)}) — اضغط للعرض
                    </summary>
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs" data-testid="cod-post-sync-orders-table">
                            <thead className="bg-slate-50 text-slate-700">
                                <tr>
                                    <th className="px-3 py-2 text-right font-bold">رقم الطلب</th>
                                    <th className="px-3 py-2 text-right font-bold">تاريخ الاستلام</th>
                                    <th className="px-3 py-2 text-right font-bold">الحالة</th>
                                    <th className="px-3 py-2 text-right font-bold">شركة الشحن</th>
                                    <th className="px-3 py-2 text-left font-bold">المبلغ</th>
                                    <th className="px-3 py-2 text-center font-bold">يدخل في Confirmed؟</th>
                                </tr>
                            </thead>
                            <tbody>
                                {orders.map((o) => (
                                    <tr key={o.order_id} className="border-t border-slate-100">
                                        <td className="px-3 py-2 text-right font-mono text-[11px]">{o.order_id}</td>
                                        <td className="px-3 py-2 text-right">
                                            {new Date(o.received_at).toLocaleString("en-GB", { timeZone: "Asia/Riyadh", hour12: false })}
                                        </td>
                                        <td className="px-3 py-2 text-right">{o.order_status}</td>
                                        <td className="px-3 py-2 text-right">{o.shipping_company}</td>
                                        <td className="px-3 py-2 num text-left font-bold">{fmt(o.gross)}</td>
                                        <td className="px-3 py-2 text-center">
                                            {o.counted_in_confirmed ? (
                                                <span className="inline-block px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 text-[10px] font-bold">✓</span>
                                            ) : (
                                                <span className="inline-block px-2 py-0.5 rounded-full bg-slate-200 text-slate-600 text-[10px] font-bold">—</span>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </details>
            )}
        </div>
    );
}

export default function CODDiagnostic() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    // Iter-189 — payment_mode lookup by company name (normalized).
    const [paymentModeMap, setPaymentModeMap] = useState({});

    const loadPaymentModes = useCallback(async () => {
        try {
            const r = await api.get("/settings");
            const map = {};
            for (const c of (r.data?.shipping_companies || [])) {
                if (c?.name) {
                    map[c.name.trim()] = c.payment_mode
                        || (c.is_deferred ? "deferred" : "prepaid");
                }
            }
            setPaymentModeMap(map);
        } catch (_) { /* swallow — badge will just show "—" */ }
    }, []);

    const load = useCallback(async (showLoading = false) => {
        if (showLoading) setLoading(true);
        setError(null);
        try {
            const res = await api.get("/diagnostics/cod-source");
            setData(res.data);
        } catch (e) {
            setError(errMsg(e, "تعذّر تحميل التقرير"));
            toast.error(errMsg(e, "تعذّر تحميل التقرير"));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const res = await api.get("/diagnostics/cod-source");
                if (alive) setData(res.data);
            } catch (e) {
                if (alive) {
                    setError(errMsg(e, "تعذّر تحميل التقرير"));
                    toast.error(errMsg(e, "تعذّر تحميل التقرير"));
                }
            } finally {
                if (alive) setLoading(false);
            }
        })();
        loadPaymentModes();
        return () => { alive = false; };
    }, []);

    if (loading) {
        return (
            <div className="p-8 text-center text-slate-500" data-testid="cod-diagnostic-loading">
                جاري تحميل تقرير COD…
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="p-8 max-w-4xl mx-auto">
                <div className="rounded-2xl border-2 border-rose-200 bg-rose-50 p-6 text-center" data-testid="cod-diagnostic-error">
                    <div className="text-rose-800 font-bold mb-2">تعذّر تحميل التقرير</div>
                    <div className="text-sm text-rose-700">{error}</div>
                    <button
                        onClick={load}
                        className="mt-4 px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white text-sm font-bold rounded"
                        data-testid="cod-retry-btn"
                    >
                        إعادة المحاولة
                    </button>
                </div>
            </div>
        );
    }

    if (!data.found_cod_account) {
        return (
            <div className="p-8 max-w-4xl mx-auto">
                <div className="rounded-2xl border-2 border-amber-200 bg-amber-50 p-6 text-center" data-testid="cod-diagnostic-not-found">
                    <div className="text-amber-900 font-bold mb-2">لا يوجد حساب COD</div>
                    <div className="text-sm text-amber-800">{data.message}</div>
                </div>
            </div>
        );
    }

    const acc = data.account;
    const rec = data.reconciliation;
    const cats = data.by_policy_category || {};
    const totalConfirmed = cats.confirmed?.gross || 0;
    const totalDelivered = (data.by_shipping_company_delivered_only || [])
        .reduce((s, r) => s + (r.gross || 0), 0);
    const totalDeliveredCount = (data.by_shipping_company_delivered_only || [])
        .reduce((s, r) => s + (r.count || 0), 0);
    const confirmedVsDelivered = totalConfirmed - totalDelivered;

    const categoryRows = [
        { key: "confirmed", count: cats.confirmed?.count || 0, gross: cats.confirmed?.gross || 0 },
        { key: "pending",   count: cats.pending?.count   || 0, gross: cats.pending?.gross   || 0 },
        { key: "cancelled", count: cats.cancelled?.count || 0, gross: cats.cancelled?.gross || 0 },
        { key: "refunded",  count: cats.refunded?.count  || 0, gross: cats.refunded?.gross  || 0 },
    ];
    if (cats._unknown?.count) {
        categoryRows.push({ key: "_unknown", count: cats._unknown.count, gross: cats._unknown.gross });
    }

    const reconDiff = rec?.diff_should_be_zero || 0;
    const reconHealthy = Math.abs(reconDiff) < 0.5;

    return (
        <div className="p-6 max-w-7xl mx-auto" data-testid="cod-diagnostic-page">
            {/* HEADER */}
            <div className="bg-white rounded-2xl shadow-lg p-6 mb-6">
                <div className="flex items-center justify-between flex-wrap gap-3">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            🔍 تقرير تشخيصي لحساب COD
                        </h1>
                        <p className="text-sm text-slate-500 mt-1">
                            مصدر الرصيد · توزيع الحالات · الطلبات حسب شركة الشحن
                        </p>
                    </div>
                    <button
                        onClick={load}
                        className="px-4 py-2 bg-slate-900 hover:bg-slate-800 text-white text-sm font-bold rounded-lg"
                        data-testid="cod-refresh-btn"
                    >
                        🔄 إعادة التحميل
                    </button>
                </div>
            </div>

            {/* HOW IT IS CALCULATED — Highlight banner */}
            <div className="bg-amber-50 border-2 border-amber-300 rounded-2xl p-5 mb-6" data-testid="cod-basis-banner">
                <div className="flex items-start gap-3">
                    <div className="text-3xl shrink-0">⚠️</div>
                    <div>
                        <h2 className="text-base font-extrabold text-amber-900 mb-1">
                            كيف يحتسب النظام الرصيد حالياً
                        </h2>
                        <p className="text-sm text-amber-800 leading-relaxed">
                            رصيد COD يُحتسب بناءً على الطلبات <strong>Confirmed</strong> (حسب سياسة <code className="bg-amber-100 px-1 rounded">order_status_policy</code>) <strong>وليس Delivered</strong> فقط.
                            يعني أي طلب أُكِّد—حتى لو لم يُسلَّم بعد—يدخل في الرصيد.
                            راجع الجداول أدناه لمعرفة الفرق الفعلي بين المُؤكَّد والمُسلَّم.
                        </p>
                    </div>
                </div>
            </div>

            {/* SUMMARY CARDS */}
            <h2 className="text-lg font-extrabold text-slate-900 mb-3 mt-2">📊 ملخّص COD</h2>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-6">
                <StatCard
                    label="رصيد COD الحالي"
                    value={fmt(acc.current_balance)}
                    sublabel="current_balance"
                    tone="emerald"
                    testid="cod-stat-current-balance"
                />
                <StatCard
                    label="الرصيد المتوقَّع من الطلبات"
                    value={fmt(acc.expected_orders_balance)}
                    sublabel="expected_orders_balance"
                    tone="slate"
                    testid="cod-stat-expected"
                />
                <StatCard
                    label="عدد طلبات COD"
                    value={intf(data.total_cod_orders_in_db)}
                    sublabel={`المخزّن: ${intf(acc.orders_count_on_account)}`}
                    tone="slate"
                    testid="cod-stat-orders-count"
                />
                <StatCard
                    label="تحويلات للبنوك (OUT)"
                    value={fmt(data.manual_transactions?.out_total)}
                    sublabel={`${intf(data.manual_transactions?.out_count)} حركة`}
                    tone="amber"
                    testid="cod-stat-out-total"
                />
                <StatCard
                    label="فرق المطابقة"
                    value={fmt(reconDiff)}
                    sublabel={reconHealthy ? "✅ متطابق" : "⚠️ يحتاج مراجعة"}
                    tone={reconHealthy ? "emerald" : "rose"}
                    testid="cod-stat-recon-diff"
                />
            </div>

            {/* RECONCILIATION FORMULA */}
            <div className="bg-white rounded-2xl shadow p-5 mb-6 border border-slate-200" data-testid="cod-reconciliation-block">
                <h3 className="font-extrabold text-slate-900 mb-3">🔄 معادلة المطابقة</h3>
                <div className="bg-slate-50 rounded-lg p-4 font-mono text-sm leading-relaxed border border-slate-200">
                    <div>
                        <span className="text-slate-500">expected_orders_balance</span>{" "}
                        = <strong className="num">{fmt(rec.expected_orders_balance)}</strong>
                    </div>
                    <div>
                        <span className="text-emerald-600">+ IN</span>{" "}
                        = <strong className="num">{fmt(rec.in_total)}</strong>
                    </div>
                    <div>
                        <span className="text-rose-600">− OUT</span>{" "}
                        = <strong className="num">{fmt(rec.out_total)}</strong>
                    </div>
                    <div className="mt-2 pt-2 border-t border-slate-300">
                        <span className="text-slate-700">= derived_balance</span>{" "}
                        = <strong className="num">{fmt(rec.derived_balance)}</strong>
                    </div>
                    <div>
                        <span className="text-slate-700">actual current_balance</span>{" "}
                        = <strong className="num">{fmt(rec.actual_current_balance)}</strong>
                    </div>
                    <div className={`mt-2 pt-2 border-t-2 ${reconHealthy ? "border-emerald-400 text-emerald-800" : "border-rose-400 text-rose-800"}`}>
                        <span>فرق (يجب أن يكون 0)</span>{" "}
                        = <strong className="num">{fmt(reconDiff)}</strong>
                        {reconHealthy ? " ✅" : " ⚠️"}
                    </div>
                </div>
                {!reconHealthy && (
                    <div className="mt-3 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">
                        {rec.interpretation}
                    </div>
                )}
            </div>

            {/* POST-SYNC DRIFT BLOCK — Iter-180 */}
            {data.post_sync_drift && (
                <PostSyncDriftBlock drift={data.post_sync_drift} />
            )}

            {/* CONFIRMED vs DELIVERED COMPARISON */}
            <div className="bg-white rounded-2xl shadow p-5 mb-6 border-2 border-emerald-200" data-testid="cod-confirmed-vs-delivered">
                <h3 className="font-extrabold text-slate-900 mb-3">
                    🎯 الفرق بين Confirmed و Delivered
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="bg-emerald-50 border border-emerald-300 rounded-xl p-4 text-center">
                        <div className="text-xs text-emerald-700 font-bold mb-1">Confirmed (المُحتسَب)</div>
                        <div className="num text-2xl font-extrabold text-emerald-900">{fmt(totalConfirmed)}</div>
                        <div className="text-[11px] text-emerald-700 mt-1">{intf(cats.confirmed?.count || 0)} طلب</div>
                    </div>
                    <div className="bg-sky-50 border border-sky-300 rounded-xl p-4 text-center">
                        <div className="text-xs text-sky-700 font-bold mb-1">Delivered فعلياً</div>
                        <div className="num text-2xl font-extrabold text-sky-900">{fmt(totalDelivered)}</div>
                        <div className="text-[11px] text-sky-700 mt-1">{intf(totalDeliveredCount)} طلب</div>
                    </div>
                    <div className={`${Math.abs(confirmedVsDelivered) < 0.5 ? "bg-emerald-50 border-emerald-300" : "bg-amber-50 border-amber-300"} border rounded-xl p-4 text-center`}>
                        <div className="text-xs font-bold mb-1 opacity-80">الفرق</div>
                        <div className="num text-2xl font-extrabold">{fmt(confirmedVsDelivered)}</div>
                        <div className="text-[11px] mt-1 opacity-70">
                            مُؤكَّد لكن لم يُسلَّم بعد
                        </div>
                    </div>
                </div>
                <div className="mt-4 text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-lg p-3 leading-relaxed">
                    <strong>الـ COD المحاسبي الحالي</strong> ({fmt(acc.current_balance)} ر.س) يشمل طلبات
                    قيد التوصيل وطلبات تم تأكيدها لكن لم تُسلَّم بعد. <strong>الـ COD الموصَّل فعلياً</strong> هو فقط
                    {" "}{fmt(totalDelivered)} ر.س — وهذا ما تم قبضه (أو في طريقه إليك من شركات الشحن).
                </div>
            </div>

            {/* BY POLICY CATEGORY */}
            <h2 className="text-lg font-extrabold text-slate-900 mb-3 mt-2">📋 توزيع طلبات COD حسب الحالة</h2>
            <div className="mb-6">
                <CategoryTable rows={categoryRows} />
            </div>

            {/* BY SHIPPING COMPANY — All */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
                <div>
                    <h3 className="text-base font-extrabold text-slate-900 mb-2">
                        🚚 كل طلبات COD حسب شركة الشحن
                    </h3>
                    <CompanyTable
                        rows={data.by_shipping_company_all || []}
                        testid="cod-company-all-table"
                        paymentModeMap={paymentModeMap}
                    />
                </div>
                <div>
                    <h3 className="text-base font-extrabold text-emerald-900 mb-2">
                        🎯 الموصَّلة فقط حسب شركة الشحن
                    </h3>
                    <CompanyTable
                        rows={data.by_shipping_company_delivered_only || []}
                        testid="cod-company-delivered-table"
                        emphasize
                        paymentModeMap={paymentModeMap}
                    />
                </div>
            </div>

            {/* RAW STATUS — collapsible-ish */}
            <details className="bg-white rounded-2xl shadow p-5 mb-6 border border-slate-200" data-testid="cod-raw-status-block">
                <summary className="cursor-pointer font-extrabold text-slate-900 select-none">
                    📑 التوزيع التفصيلي حسب الحالة الخام (raw status)
                </summary>
                <div className="mt-3">
                    <RawStatusTable rows={data.by_raw_status || []} />
                </div>
            </details>

            {/* DECISION HELP */}
            <div className="bg-gradient-to-l from-indigo-50 to-white border-2 border-indigo-200 rounded-2xl p-5" data-testid="cod-decision-help">
                <h3 className="font-extrabold text-indigo-900 mb-2">📌 ماذا يعني هذا للترحيل النهائي؟</h3>
                <ul className="text-sm text-indigo-900 space-y-1.5 list-disc pr-5">
                    <li>
                        إذا رحَّلت <strong>{fmt(acc.current_balance)} ر.س</strong> كقيد افتتاحي،
                        فأنت ترحّل طلبات مُؤكَّدة <em>وغير مُسلَّمة بعد</em>.
                    </li>
                    <li>
                        إذا أردت ترحيل <strong>الموصَّل فعلياً فقط</strong>،
                        الرقم سيكون: <strong className="num">{fmt(totalDelivered)} ر.س</strong>.
                    </li>
                    <li>
                        الفرق <strong className="num">{fmt(confirmedVsDelivered)} ر.س</strong>{" "}
                        يمثّل طلبات في الطريق إليك (لم يُحصَّل كاش بعد).
                    </li>
                    <li>
                        النظام حالياً <strong>لا يربط</strong> هذه الأرقام بحسابات شركات الشحن
                        المنفصلة (SMSA / iMile / مندوب الرياض) في الـ Ledger —
                        هذا سيتم في Sprint الشحن لاحقاً.
                    </li>
                </ul>
            </div>
        </div>
    );
}
