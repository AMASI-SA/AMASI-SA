/**
 * BNPL Diagnostics — Iter-116 verification dashboard.
 *
 * Proves the sync actually fetched data (and proves WHY when it didn't).
 * Answers all 8 questions the merchant asked:
 *   1. last sync timestamps
 *   2. sync scope (which since-date)
 *   3. matching counts
 *   4. refund counts
 *   5. mismatch report
 *   6. comparison table
 *   7. dedup audit
 *   8. old-orders behaviour contract
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    Stethoscope, ArrowsClockwise, CheckCircle, XCircle,
    Warning, ArrowLeft, MagnifyingGlass, Receipt,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const errMsg = (e, fb) =>
    formatApiErrorDetail(e?.response?.data?.detail) || fb || "حدث خطأ";

const fmtMoney = (n) => {
    const v = Number(n || 0);
    return v.toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
};

const fmtDate = (iso) => {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString("ar-SA");
    } catch {
        return iso;
    }
};

function StatTile({ label, value, hint, tone = "slate", testid }) {
    const toneCls = {
        slate: "border-slate-200 bg-white",
        emerald: "border-emerald-200 bg-emerald-50",
        amber: "border-amber-200 bg-amber-50",
        rose: "border-rose-200 bg-rose-50",
        sky: "border-sky-200 bg-sky-50",
    }[tone] || "border-slate-200 bg-white";
    return (
        <div className={`rounded-lg border ${toneCls} p-3`} data-testid={testid}>
            <div className="text-[10px] font-bold text-slate-600">{label}</div>
            <div className="num text-xl font-extrabold text-slate-900 mt-0.5">{value}</div>
            {hint && <div className="text-[10px] text-slate-500 mt-1">{hint}</div>}
        </div>
    );
}

function ProviderSection({ provider, label, data, onSync }) {
    if (!data) return null;
    const s = data.settings || {};
    const stats = data.stats || {};
    const matching = data.matching || {};
    const refunds = data.refunds || {};
    const unmatched = data.samples_unmatched_payments || [];

    const supportsSync = provider === "tabby";

    return (
        <div className="rounded-2xl border border-slate-200 bg-white p-5" data-testid={`bnpl-diag-${provider}`}>
            <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                    {label}
                    {s.enabled
                        ? <span className="text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">مفعّل</span>
                        : <span className="text-[10px] font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">معطّل</span>}
                </h2>
                {supportsSync && (
                    <button
                        type="button"
                        onClick={() => onSync(provider)}
                        className="text-xs bg-emerald-600 text-white font-bold px-3 py-1.5 rounded-lg hover:bg-emerald-700 flex items-center gap-1"
                        data-testid={`bnpl-diag-${provider}-sync`}
                    >
                        <ArrowsClockwise size={14} /> مزامنة الآن
                    </button>
                )}
            </div>

            {/* Section 1 — Last sync state */}
            <div className="mb-3">
                <h3 className="text-xs font-extrabold text-slate-500 mb-1">1) آخر مزامنة</h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <StatTile
                        label="آخر مزامنة API"
                        value={fmtDate(s.last_sync_at)}
                        tone={s.last_sync_at ? "emerald" : "slate"}
                        testid={`stat-${provider}-last-sync`}
                    />
                    <StatTile
                        label="آخر webhook مستلَم"
                        value={fmtDate(s.last_webhook_at)}
                        tone={s.last_webhook_at ? "emerald" : "slate"}
                        testid={`stat-${provider}-last-webhook`}
                    />
                    <StatTile
                        label="عدد المعاملات المحفوظة"
                        value={Number(stats.transactions_count || 0).toLocaleString("en-US")}
                        tone={stats.transactions_count > 0 ? "emerald" : "amber"}
                        testid={`stat-${provider}-tx-count`}
                    />
                    <StatTile
                        label="عدد المسترجعات المحفوظة"
                        value={Number(stats.refunds_count || 0).toLocaleString("en-US")}
                        tone={stats.refunds_count > 0 ? "sky" : "slate"}
                        testid={`stat-${provider}-rf-count`}
                    />
                </div>
            </div>

            {/* Section 2 — Sync scope */}
            <div className="mb-3 rounded-lg bg-slate-50 border border-slate-200 p-3 text-xs">
                <h3 className="text-xs font-extrabold text-slate-700 mb-1">2) نطاق المزامنة</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-slate-700">
                    <div>تاريخ تفعيل الربط: <b className="text-slate-900">{s.activation_date || "غير محدّد"}</b></div>
                    <div>أقدم عملية محفوظة: <b className="text-slate-900">{fmtDate(stats.earliest_payment_at)}</b></div>
                    <div>أحدث عملية محفوظة: <b className="text-slate-900">{fmtDate(stats.latest_payment_at)}</b></div>
                    <div>القاعدة: <span className="text-slate-500">«من تاريخ التفعيل فقط — لا backfill تاريخي إلا بطلب صريح»</span></div>
                </div>
            </div>

            {/* Section 3 — Matching */}
            <div className="mb-3">
                <h3 className="text-xs font-extrabold text-slate-500 mb-1">3) مطابقة الطلبات</h3>
                <div className="grid grid-cols-3 gap-2">
                    <StatTile
                        label="معاملات مرتبطة بطلبات موجودة"
                        value={Number(matching.matched_orders || 0).toLocaleString("en-US")}
                        tone="emerald"
                        hint="موجود من Make/Excel"
                        testid={`stat-${provider}-matched`}
                    />
                    <StatTile
                        label="طلبات أنشأها المزود تلقائياً"
                        value={Number(matching.created_orders || 0).toLocaleString("en-US")}
                        tone="sky"
                        hint="needs_review = true"
                        testid={`stat-${provider}-created`}
                    />
                    <StatTile
                        label="معاملات غير مطابقة بعد"
                        value={Number(matching.unmatched_txns || 0).toLocaleString("en-US")}
                        tone={matching.unmatched_txns > 0 ? "rose" : "slate"}
                        testid={`stat-${provider}-unmatched`}
                    />
                </div>
            </div>

            {/* Section 4 — Refunds */}
            <div className="mb-3">
                <h3 className="text-xs font-extrabold text-slate-500 mb-1">4) المسترجعات</h3>
                <div className="grid grid-cols-2 gap-2">
                    <StatTile
                        label="Refunds مكتشفة من المزوّد"
                        value={Number(refunds.refunds_detected || 0).toLocaleString("en-US")}
                        tone={refunds.refunds_detected > 0 ? "sky" : "slate"}
                        testid={`stat-${provider}-refunds`}
                    />
                    <StatTile
                        label="طلبات حُدّثت بمسترجعات"
                        value={Number(refunds.orders_with_refunds_from_provider || 0).toLocaleString("en-US")}
                        tone="emerald"
                        testid={`stat-${provider}-orders-with-refunds`}
                    />
                </div>
            </div>

            {/* Section 5 — Mismatch sample */}
            {unmatched.length > 0 && (
                <div className="mb-1">
                    <h3 className="text-xs font-extrabold text-rose-600 mb-1 flex items-center gap-1">
                        <Warning size={14} weight="duotone" />
                        5) عيّنة من معاملات موجودة في {label} وغير مرتبطة بطلب
                    </h3>
                    <div className="rounded-lg border border-rose-200 bg-white overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead className="bg-rose-50 text-rose-900">
                                <tr>
                                    <th className="p-2 text-right">Provider Payment ID</th>
                                    <th className="p-2 text-right">Order Ref</th>
                                    <th className="p-2 text-right">Status</th>
                                    <th className="p-2 text-right">Amount</th>
                                    <th className="p-2 text-right">Created</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-rose-100">
                                {unmatched.map((u, i) => (
                                    <tr key={i} data-testid={`bnpl-diag-${provider}-unmatched-row-${i}`}>
                                        <td className="p-2 font-mono text-[10px]">{u.provider_id}</td>
                                        <td className="p-2 text-slate-700">{u.order_reference_id || "—"}</td>
                                        <td className="p-2"><span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 text-[10px]">{u.status}</span></td>
                                        <td className="p-2 num">{fmtMoney(u.amount)} {u.currency || ""}</td>
                                        <td className="p-2 text-[10px] text-slate-500">{fmtDate(u.created_at_provider)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}

export default function BnplDiagnostics() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState("all");

    const load = async () => {
        try {
            const { data } = await api.get("/bnpl/diagnostics");
            setData(data);
        } catch (e) {
            toast.error(errMsg(e, "تعذّر التحميل"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const { data } = await api.get("/bnpl/diagnostics");
                if (!cancelled) setData(data);
            } catch (e) {
                if (!cancelled) toast.error(errMsg(e, "تعذّر التحميل"));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, []);

    const runSync = async (provider) => {
        try {
            const { data } = await api.post(`/bnpl/${provider}/sync`);
            const s = data.stats || {};
            const fetched = Number(s.fetched || 0);
            if (fetched === 0) {
                toast.warning(
                    `Tabby أرجع 0 معاملات في النطاق المطلوب — ` +
                    `جرّب «جلب تاريخي» بتاريخ أقدم.`,
                    { duration: 6000 },
                );
            } else {
                toast.success(
                    `استلَم Tabby ${fetched} معاملة · ` +
                    `حُفظ: ${s.transactions_upserted || 0} · ` +
                    `مسترجعات: ${s.refunds_upserted || 0} · ` +
                    `طلبات جديدة: ${s.orders_created || 0}`,
                );
            }
            await load();
        } catch (e) {
            toast.error(errMsg(e, "فشلت المزامنة"));
        }
    };

    const runBackfill = async () => {
        const today = new Date().toISOString().slice(0, 10);
        const def = "2026-01-01";
        const since = window.prompt(
            `أدخل تاريخ بداية الجلب التاريخي (YYYY-MM-DD)\n` +
            `سيقوم النظام بجلب كل معاملات Tabby من هذا التاريخ حتى اليوم (${today}).\n` +
            `الحد الأقصى المنصوح: 6-12 شهراً سابقة.`,
            def,
        );
        if (!since) return;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(since)) {
            toast.error("صيغة التاريخ خطأ. يجب أن تكون YYYY-MM-DD");
            return;
        }
        try {
            toast.info("جاري الجلب التاريخي… قد يستغرق دقيقة");
            const { data } = await api.post(`/bnpl/tabby/sync?since=${since}`);
            const s = data.stats || {};
            const fetched = Number(s.fetched || 0);
            if (fetched === 0) {
                toast.warning(
                    `لم يُرجع Tabby أي معاملات منذ ${since}. ` +
                    `قد لا تكون البيانات في فترة activation_date للمتجر بعد، ` +
                    `أو الحساب جديد.`,
                    { duration: 8000 },
                );
            } else {
                toast.success(
                    `جلب تاريخي اكتمل ← ${fetched} معاملة · ` +
                    `حُفظ: ${s.transactions_upserted || 0} · ` +
                    `مسترجعات: ${s.refunds_upserted || 0} · ` +
                    `طلبات جديدة: ${s.orders_created || 0}`,
                    { duration: 8000 },
                );
            }
            await load();
        } catch (e) {
            toast.error(errMsg(e, "فشل الجلب التاريخي"));
        }
    };

    const runTamaraBackfill = async () => {
        const def = "2026-01-01";
        const since = window.prompt(
            `أدخل تاريخ أقدم طلب تريد فحصه في Tamara (YYYY-MM-DD).\n` +
            `النظام سيمرّ على طلبات unified_orders بدءاً من هذا التاريخ ` +
            `ويستفسر عن كل order_reference_id من Tamara.\n` +
            `قد يستغرق دقائق حسب عدد الطلبات.`,
            def,
        );
        if (!since) return;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(since)) {
            toast.error("صيغة التاريخ خطأ. يجب أن تكون YYYY-MM-DD");
            return;
        }
        const limit = window.prompt(
            `الحد الأقصى لعدد الطلبات في هذه الدفعة (1-5000)`,
            "500",
        );
        if (!limit) return;
        try {
            toast.info("جاري الفحص التاريخي لـ Tamara… لا تُغلق الصفحة");
            const { data } = await api.post(
                `/bnpl/tamara/backfill?since=${since}&limit=${parseInt(limit, 10) || 500}`,
            );
            const s = data.stats || {};
            toast.success(
                `فحص Tamara اكتمل ← ` +
                `تم الفحص: ${s.scanned || 0} · ` +
                `موجودة: ${s.found || 0} · ` +
                `غير موجودة: ${s.not_found || 0} · ` +
                `مسترجعات: ${s.refunds_found || 0} · ` +
                `طلبات حُدّثت: ${s.orders_updated || 0}` +
                (s.errors ? ` · أخطاء: ${s.errors}` : ""),
                { duration: 12000 },
            );
            await load();
        } catch (e) {
            toast.error(errMsg(e, "فشل فحص Tamara"));
        }
    };

    if (loading && !data) {
        return <div className="p-6 text-center text-slate-500">جاري تجميع التقرير…</div>;
    }
    if (!data) return null;

    const headline = data.headline || {};
    const providers = data.providers || {};
    const rows = (data.comparison_rows || []).filter((r) =>
        filter === "all" ? true : r.provider === filter,
    );

    return (
        <div className="space-y-4" dir="rtl" data-testid="bnpl-diagnostics-page">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                        <Stethoscope size={26} weight="duotone" className="text-sky-700" />
                        تقرير تشخيص ربط Tamara و Tabby
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        إثبات فعلي من قاعدة البيانات أن المزامنة جلبت بيانات حقيقية.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Link
                        to="/integrations/bnpl"
                        className="text-xs text-slate-600 hover:text-slate-900 flex items-center gap-1"
                        data-testid="bnpl-diag-back"
                    >
                        <ArrowLeft size={14} /> العودة للإعدادات
                    </Link>
                    <button
                        type="button"
                        onClick={runBackfill}
                        className="px-3 py-1.5 bg-amber-600 text-white text-xs font-bold rounded-lg hover:bg-amber-700 flex items-center gap-1"
                        data-testid="bnpl-diag-backfill"
                    >
                        <ArrowsClockwise size={14} /> جلب تاريخي Tabby
                    </button>
                    <button
                        type="button"
                        onClick={runTamaraBackfill}
                        className="px-3 py-1.5 bg-purple-600 text-white text-xs font-bold rounded-lg hover:bg-purple-700 flex items-center gap-1"
                        data-testid="bnpl-diag-tamara-backfill"
                    >
                        <ArrowsClockwise size={14} /> فحص تاريخي Tamara
                    </button>
                    <button
                        type="button"
                        onClick={() => { setLoading(true); load(); }}
                        className="px-3 py-1.5 bg-slate-900 text-white text-xs font-bold rounded-lg hover:bg-slate-800 flex items-center gap-1"
                        data-testid="bnpl-diag-refresh"
                    >
                        <ArrowsClockwise size={14} /> تحديث التقرير
                    </button>
                </div>
            </div>

            {/* Headline KPIs */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <StatTile
                    label="إجمالي المعاملات (Tabby + Tamara)"
                    value={Number(headline.total_transactions_synced || 0).toLocaleString("en-US")}
                    tone={headline.total_transactions_synced > 0 ? "emerald" : "amber"}
                    testid="bnpl-diag-headline-tx"
                />
                <StatTile
                    label="إجمالي المسترجعات"
                    value={Number(headline.total_refunds_synced || 0).toLocaleString("en-US")}
                    tone="sky"
                    testid="bnpl-diag-headline-rf"
                />
                <StatTile
                    label="طلبات مرتبطة بالنظام"
                    value={Number(headline.total_orders_matched || 0).toLocaleString("en-US")}
                    tone="emerald"
                    testid="bnpl-diag-headline-matched"
                />
                <StatTile
                    label="طلبات أنشأها المزود"
                    value={Number(headline.total_orders_created_by_bnpl || 0).toLocaleString("en-US")}
                    tone="sky"
                    hint="needs_review = true"
                    testid="bnpl-diag-headline-created"
                />
            </div>

            {/* Per-provider sections */}
            <ProviderSection provider="tabby" label="Tabby" data={providers.tabby} onSync={runSync} />
            <ProviderSection provider="tamara" label="Tamara" data={providers.tamara} onSync={runSync} />

            {/* Section 6 — Comparison table */}
            <div className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="bnpl-diag-comparison">
                <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                    <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                        <MagnifyingGlass size={20} weight="duotone" className="text-slate-700" />
                        6) جدول المقارنة الموحّد
                    </h2>
                    <div className="flex items-center gap-1">
                        {["all", "tabby", "tamara"].map((p) => (
                            <button
                                key={p}
                                type="button"
                                onClick={() => setFilter(p)}
                                className={`px-2.5 py-1 text-xs font-bold rounded-lg ${
                                    filter === p
                                        ? "bg-slate-900 text-white"
                                        : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                                }`}
                                data-testid={`bnpl-diag-filter-${p}`}
                            >
                                {p === "all" ? "الكل" : p.charAt(0).toUpperCase() + p.slice(1)}
                            </button>
                        ))}
                    </div>
                </div>

                {rows.length === 0 ? (
                    <div className="text-center py-8 text-sm text-slate-500" data-testid="bnpl-diag-empty">
                        لا توجد معاملات مزامَنة بعد. اضغط «مزامنة الآن» في القسم العلوي.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs" data-testid="bnpl-diag-table">
                            <thead className="bg-slate-50 text-slate-700">
                                <tr>
                                    <th className="p-2 text-right">Order #</th>
                                    <th className="p-2 text-right">Provider</th>
                                    <th className="p-2 text-right">Payment Status</th>
                                    <th className="p-2 text-right">Amount</th>
                                    <th className="p-2 text-right">Refunded</th>
                                    <th className="p-2 text-right">Match</th>
                                    <th className="p-2 text-right">Date</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {rows.map((r, i) => (
                                    <tr key={`${r.provider}-${r.provider_id}-${i}`} className="hover:bg-slate-50">
                                        <td className="p-2 font-bold text-slate-900">{r.order_number}</td>
                                        <td className="p-2">
                                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                                r.provider === "tabby" ? "bg-purple-100 text-purple-700" : "bg-amber-100 text-amber-700"
                                            }`}>
                                                {r.provider}
                                            </span>
                                        </td>
                                        <td className="p-2 text-slate-700">{r.payment_status || "—"}</td>
                                        <td className="p-2 num">{fmtMoney(r.amount)}</td>
                                        <td className="p-2 num text-rose-700">{r.refund_amount > 0 ? fmtMoney(r.refund_amount) : "—"}</td>
                                        <td className="p-2">
                                            {r.match_status === "matched" && (
                                                <span className="flex items-center gap-1 text-emerald-700">
                                                    <CheckCircle size={12} weight="fill" /> مرتبط
                                                </span>
                                            )}
                                            {r.match_status === "created_by_provider" && (
                                                <span className="flex items-center gap-1 text-sky-700">
                                                    <Receipt size={12} weight="fill" /> أنشأه المزوّد
                                                </span>
                                            )}
                                            {r.match_status === "unmatched" && (
                                                <span className="flex items-center gap-1 text-rose-700">
                                                    <XCircle size={12} weight="fill" /> غير مطابق
                                                </span>
                                            )}
                                            {r.match_status === "no_reference" && (
                                                <span className="flex items-center gap-1 text-amber-700">
                                                    <Warning size={12} weight="fill" /> بدون مرجع
                                                </span>
                                            )}
                                        </td>
                                        <td className="p-2 text-[10px] text-slate-500">{fmtDate(r.created_at_provider)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Sections 7 + 8 — Contracts */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5" data-testid="bnpl-diag-dedup">
                    <h2 className="text-base font-extrabold text-emerald-900 mb-2">
                        7) ضمان عدم التكرار
                    </h2>
                    <div className="space-y-1 text-xs text-emerald-900">
                        <div><b>مفتاح المطابقة (Transactions):</b> <code className="bg-white px-1.5 py-0.5 rounded text-[10px]">{data.deduplication?.transactions_unique_key}</code></div>
                        <div><b>Index:</b> <code className="bg-white px-1.5 py-0.5 rounded text-[10px]">{data.deduplication?.transactions_index}</code></div>
                        <div><b>مفتاح المطابقة (Refunds):</b> <code className="bg-white px-1.5 py-0.5 rounded text-[10px]">{data.deduplication?.refunds_unique_key}</code></div>
                        <div className="pt-1 leading-relaxed">{data.deduplication?.guarantee}</div>
                    </div>
                </div>

                <div className="rounded-2xl border border-sky-200 bg-sky-50 p-5" data-testid="bnpl-diag-behavior">
                    <h2 className="text-base font-extrabold text-sky-900 mb-2">
                        8) سلوك الطلبات القديمة
                    </h2>
                    <div className="space-y-1 text-xs text-sky-900">
                        <div><b>الوضع:</b> <span className="inline-block px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 text-[10px] font-bold">{data.old_orders_behavior?.mode}</span></div>
                        <div className="pt-1 leading-relaxed">{data.old_orders_behavior?.explanation}</div>
                        <div className="pt-1">
                            <b>أولوية المطابقة:</b>{" "}
                            {(data.old_orders_behavior?.match_keys_priority || []).map((k) => (
                                <code key={k} className="bg-white px-1.5 py-0.5 rounded text-[10px] ms-1">{k}</code>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            <div className="text-[10px] text-slate-400 text-left font-mono">
                Report generated at {fmtDate(data.generated_at)} — User: {data.user_id}
            </div>
        </div>
    );
}
