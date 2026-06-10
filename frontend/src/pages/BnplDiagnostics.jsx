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
    // Live Tabby backfill job state (Iter-116 — manual Start/Continue UI)
    const [tabbyJob, setTabbyJob] = useState(null);   // last batch response
    const [tabbyBusy, setTabbyBusy] = useState(false);

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
                window.alert(
                    `Tabby Sync — 0 معاملة\n\n` +
                    `Endpoint: ${s.filter_used?.endpoint || "?"}\n` +
                    `Filter: created_at__gte=${s.filter_used?.created_at__gte || "?"}\n` +
                    `Status filter: ${s.filter_used?.status || "?"}\n\n` +
                    `${s.diagnosis || "Tabby أرجع 0 معاملات في النطاق."}`,
                );
            } else {
                const r = s.reconciliation || {};
                toast.success(
                    `Tabby ← استلم: ${r.fetched} · حُفظ: ${r.saved}` +
                    (r.missing > 0 ? ` · مفقود: ${r.missing}` : "") +
                    ` · أول تاريخ: ${(s.first_payment_created_at || "").slice(0, 10)}` +
                    ` · آخر تاريخ: ${(s.last_payment_created_at || "").slice(0, 10)}`,
                    { duration: 14000 },
                );
            }
            console.log("Sync report →", data);
            await load();
        } catch (e) {
            toast.error(errMsg(e, "فشلت المزامنة"));
        }
    };

    const runBackfill = async () => {
        const today = new Date().toISOString().slice(0, 10);
        const def = "2025-01-01";
        const since = window.prompt(
            `🔁 جلب تاريخي Tabby (دفعات صغيرة آمنة)\n\n` +
            `كل دفعة = 100 معاملة (5 صفحات × 20).\n` +
            `النظام يستأنف تلقائياً حتى الانتهاء.\n\n` +
            `أدخل التاريخ الأقدم (YYYY-MM-DD):`,
            def,
        );
        if (!since) return;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(since)) {
            toast.error("صيغة التاريخ خطأ. يجب YYYY-MM-DD");
            return;
        }

        let jobId = null;
        let totals = { fetched: 0, saved: 0, pages_read: 0,
                       first_date: null, last_date: null, stop_reason: "" };
        try {
            toast.info("بدأ الجلب التاريخي…", { duration: 3000 });
            const startRes = await api.post(
                `/bnpl/tabby/backfill/start?cutoff=${since}`,
                null, { timeout: 90000 },
            );
            const startData = startRes.data || {};
            jobId = startData.job_id;
            if (!jobId) throw new Error("لم يبدأ الـ Job");
            totals = {
                fetched: startData.total_fetched ?? startData.fetched ?? 0,
                saved: startData.total_saved ?? startData.saved ?? 0,
                pages_read: startData.pages_read || 0,
                first_date: startData.oldest_date_seen || startData.first_date,
                last_date: startData.newest_date_seen || startData.last_date,
                stop_reason: startData.stop_reason || "",
            };
            setTabbyJob({ job_id: jobId, status: startData.status, ...totals });

            // Auto-loop until status=done (or error).  Cap at 200 batches
            // = 20,000 payments which is more than any merchant.
            let status = startData.status;
            for (let i = 0; i < 200 && status === "running"; i += 1) {
                toast.info(
                    `جاري المعالجة… دفعة ${i + 2} · معاملات: ${totals.fetched} · ` +
                    `صفحات: ${totals.pages_read}` +
                    (totals.last_date ? ` · آخر تاريخ: ${(totals.last_date || "").slice(0, 10)}` : ""),
                    { duration: 2500 },
                );
                const next = await api.post(
                    `/bnpl/tabby/backfill/continue/${jobId}`,
                    null, { timeout: 90000 },
                );
                const d = next.data || {};
                if (!d.ok) {
                    throw new Error(d.error || "Batch failed");
                }
                totals = {
                    fetched: d.total_fetched ?? d.fetched ?? totals.fetched,
                    saved: d.total_saved ?? d.saved ?? totals.saved,
                    pages_read: d.pages_read || totals.pages_read,
                    first_date: d.oldest_date_seen || d.first_date || totals.first_date,
                    last_date: d.newest_date_seen || d.last_date || totals.last_date,
                    stop_reason: d.stop_reason || totals.stop_reason,
                };
                status = d.status;
                setTabbyJob({ job_id: jobId, status, ...totals });
            }

            window.alert(
                `✅ Tabby Backfill complete\n\n` +
                `Job ID:        ${jobId}\n` +
                `Pages read:    ${totals.pages_read}\n` +
                `Fetched:       ${totals.fetched}\n` +
                `Saved:         ${totals.saved}\n` +
                `Oldest date:   ${totals.first_date}\n` +
                `Newest date:   ${totals.last_date}\n` +
                `Stop reason:   ${totals.stop_reason}\n` +
                `Final status:  ${status}`,
            );
            await load();
        } catch (e) {
            const det = e?.response?.data || {};
            toast.error(
                `فشل الجلب: ${det.error || e.message || "غير معروف"}` +
                (jobId ? ` · Job=${jobId} (يمكن استئنافه يدوياً)` : ""),
                { duration: 10000 },
            );
        }
    };

    // ── Iter-116 manual Start/Continue (single-batch buttons) ──────
    const _applyBatch = (d, prev) => ({
        job_id: d.job_id || prev?.job_id,
        status: d.status || prev?.status,
        pages_read: d.pages_read ?? prev?.pages_read ?? 0,
        total_fetched: d.total_fetched ?? d.fetched ?? prev?.total_fetched ?? 0,
        total_saved: d.total_saved ?? d.saved ?? prev?.total_saved ?? 0,
        skipped_old: d.skipped_old ?? prev?.skipped_old ?? 0,
        oldest_date_seen: d.oldest_date_seen || d.first_date || prev?.oldest_date_seen || "",
        newest_date_seen: d.newest_date_seen || d.last_date || prev?.newest_date_seen || "",
        stop_reason: d.stop_reason || prev?.stop_reason || "",
        last_offset: d.last_offset ?? prev?.last_offset ?? 0,
        batch_duration_seconds: d.batch_duration_seconds ?? 0,
    });

    const startTabbyBackfill = async () => {
        const since = window.prompt(
            `▶️ Start Tabby Backfill\n\nأدخل تاريخ التفعيل (YYYY-MM-DD)\nسيتم تجاهل أي معاملة أقدم منه:`,
            "2025-01-01",
        );
        if (!since) return;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(since)) {
            toast.error("صيغة التاريخ خطأ. يجب YYYY-MM-DD");
            return;
        }
        setTabbyBusy(true);
        try {
            const { data: d } = await api.post(
                `/bnpl/tabby/backfill/start?cutoff=${since}`,
                null, { timeout: 90000 },
            );
            if (!d.ok) throw new Error(d.error || "start failed");
            setTabbyJob(_applyBatch(d, null));
            toast.success(
                `✅ Start: حُفظ ${d.total_saved ?? 0} / تم جلب ${d.total_fetched ?? 0} · ` +
                `صفحات ${d.pages_read} · stop_reason=${d.stop_reason}`,
                { duration: 6000 },
            );
        } catch (e) {
            toast.error(errMsg(e, "فشل Start") + ` · ${e.message || ""}`, { duration: 8000 });
        } finally {
            setTabbyBusy(false);
        }
    };

    const continueTabbyBackfill = async () => {
        if (!tabbyJob?.job_id) {
            toast.error("لا يوجد Job نشط — اضغط Start أولاً");
            return;
        }
        if (tabbyJob.status === "done") {
            toast.info("الـ Job مكتمل بالفعل (status=done)");
            return;
        }
        setTabbyBusy(true);
        try {
            const { data: d } = await api.post(
                `/bnpl/tabby/backfill/continue/${tabbyJob.job_id}`,
                null, { timeout: 90000 },
            );
            if (!d.ok) throw new Error(d.error || "continue failed");
            setTabbyJob((prev) => _applyBatch(d, prev));
            toast.success(
                `✅ Continue: حُفظ ${d.total_saved ?? 0} / جلب ${d.total_fetched ?? 0} · ` +
                `stop_reason=${d.stop_reason} · status=${d.status}`,
                { duration: 6000 },
            );
            if (d.status === "done") {
                await load();
            }
        } catch (e) {
            toast.error(errMsg(e, "فشل Continue") + ` · ${e.message || ""}`, { duration: 8000 });
        } finally {
            setTabbyBusy(false);
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
                `Tamara batch ← ` +
                `مرشّحون: ${s.total_orders_candidates || 0} · ` +
                `مفحوص: ${s.scanned_count || 0} · ` +
                `موجود: ${s.found || 0} · ` +
                `غير موجود: ${s.not_found || 0} · ` +
                `مسترجعات: ${s.refunds_found || 0} · ` +
                `متبقّي: ${s.remaining_after_run || 0}` +
                (s.errors ? ` · أخطاء: ${s.errors}` : ""),
                { duration: 12000 },
            );
            await load();
        } catch (e) {
            toast.error(errMsg(e, "فشل فحص Tamara"));
        }
    };

    const runTamaraFullBackfill = async () => {
        const def = "2025-01-01";
        const since = window.prompt(
            `🔁 الفحص الكامل — يفحص كل الطلبات في النطاق دون توقّف.\n\n` +
            `أدخل التاريخ الأقدم (YYYY-MM-DD):`,
            def,
        );
        if (!since) return;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(since)) {
            toast.error("صيغة التاريخ خطأ. يجب أن تكون YYYY-MM-DD");
            return;
        }
        if (!window.confirm(
            `سيبدأ الفحص الكامل من ${since} حتى آخر طلب.\n` +
            `قد يستغرق ${10}-${30} دقيقة حسب عدد الطلبات.\n` +
            `لا تغلق الصفحة. هل تريد المتابعة؟`,
        )) return;
        try {
            toast.info("بدأ الفحص الكامل — جاري المعالجة بدفعات…", { duration: 5000 });
            const { data } = await api.post(`/bnpl/tamara/backfill/full?since=${since}`, null, { timeout: 600000 });
            const s = data.stats || {};
            toast.success(
                `🎉 الفحص الكامل اكتمل! ` +
                `${s.batches_completed || 0} دفعة · ` +
                `مفحوص: ${s.scanned_count || 0}/${s.total_orders_candidates || 0} · ` +
                `موجود: ${s.found || 0} · ` +
                `غير موجود: ${s.not_found || 0} · ` +
                `مسترجعات: ${s.refunds_found || 0} · ` +
                `أخطاء: ${s.errors || 0}` +
                (s.aborted ? `\n⚠ ${s.aborted}` : ""),
                { duration: 20000 },
            );
            await load();
        } catch (e) {
            toast.error(errMsg(e, "فشل الفحص الكامل"));
        }
    };

    const runRebuildRefunds = async () => {
        if (!window.confirm(
            "سيُعاد بناء سجلات payment_refunds من معاملات Tamara " +
            "التي لديها refunded_amount > 0 ولا يوجد لها سجل refund.\n\n" +
            "العملية idempotent — لن تُنشئ تكرارات. هل تكمل؟",
        )) return;
        try {
            toast.info("جاري إعادة بناء سجلات الـ refund…", { duration: 2000 });
            const { data } = await api.post(`/bnpl/tamara/rebuild-refunds`);
            console.log("Rebuild refunds →", data);
            window.alert(
                `🔧 REBUILD COMPLETE\n\n` +
                `Scanned transactions:      ${data.scanned_transactions}\n` +
                `Refund records created:    ${data.refund_records_created}\n` +
                `Already had records:       ${data.already_had_records}\n` +
                `Total amount reconstructed: ${Number(data.total_amount_reconstructed || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })} SAR\n` +
                `unified_orders synced:     ${data.unified_orders_synced}\n\n` +
                `📋 ${data.verdict}`,
            );
        } catch (e) {
            toast.error(errMsg(e, "فشل rebuild refunds"));
        }
    };


    const runTamaraRefundInspect = async () => {
        try {
            toast.info("جاري فحص أول طلب refunded من Tamara…", { duration: 3000 });
            const { data } = await api.post(`/bnpl/tamara/refund-inspect`);
            console.log("Tamara refund inspect →", data);
            if (data.note) {
                window.alert(`ℹ️ ${data.note}`);
                return;
            }
            const s = data.sample_order_used || {};
            const byId = data.by_order_id || {};
            const byRef = data.by_reference_id || {};
            const fmt = (lbl, v) => {
                if (v.error) return `  ❌ ${lbl}: ${v.error}`;
                return [
                    `  ${lbl}:`,
                    `    status:                    ${v.status}`,
                    `    total_amount:              ${JSON.stringify(v.total_amount)}`,
                    `    total_refunded_amount:     ${JSON.stringify(v.total_refunded_amount)}`,
                    `    refunded_amount:           ${JSON.stringify(v.refunded_amount)}`,
                    `    refunds[] length:          ${v.refunds_array_len}`,
                    `    refund_orders[] length:    ${v.refund_orders_array_len}`,
                    `    captures[] count:          ${v.captures_count}`,
                    `    captures[0].refunds len:   ${v.first_capture_refunds_len}`,
                    `    all top-level keys:        ${(v.all_top_level_keys || []).join(", ")}`,
                ].join("\n");
            };
            const lines = [
                `🔍 TAMARA REFUND INSPECT`,
                ``,
                `Sample order:`,
                `  reference_id:  ${s.order_reference_id}`,
                `  tamara id:     ${s.tamara_order_id}`,
                `  local status:  ${s.status_in_local_data}`,
                `  local refund:  ${s.refunded_amount_in_local}`,
                ``,
                fmt("by_order_id", byId),
                ``,
                fmt("by_reference_id", byRef),
            ].join("\n");
            window.alert(lines);
        } catch (e) {
            toast.error(errMsg(e, "فشل refund inspect"));
        }
    };


    const runTabbySyncDebug = async () => {
        try {
            toast.info("جاري فحص 5 variations من Tabby…", { duration: 3000 });
            const { data } = await api.post(`/bnpl/tabby/sync-debug`);
            console.log("Tabby sync-debug full →", data);
            const vars_ = data.variations || {};
            const lines = [
                `🔬 TABBY SYNC FORENSIC DEBUG`,
                ``,
                `Base URL:        ${data.base_url}`,
                `Merchant code:   ${data.merchant_code}`,
                `Activation date: ${data.activation_date_used}`,
                ``,
                `─── Counts ─────────────────────`,
                ...Object.entries(data.counts_summary || {})
                    .map(([k, v]) => `  ${k.padEnd(22)} → ${v}`),
                ``,
                `─── Detail per variation ───────`,
                ...Object.entries(vars_).flatMap(([label, v]) => [
                    ``,
                    `▶ ${label}`,
                    `  HTTP: ${v.http_status}  ·  count: ${v.raw_payments_count}`,
                    `  URL: ${v.full_url || "(error)"}`,
                    `  Params: ${JSON.stringify(v.params_sent)}`,
                    v.pagination ? `  Pagination.total_count: ${v.pagination?.total_count}` : "",
                    (v.payment_dates && v.payment_dates.length > 0)
                        ? `  Sample dates: ${v.payment_dates.slice(0, 5).join(", ")}`
                        : "",
                    v.error ? `  ❌ ${v.error}` : "",
                ]),
                ``,
                `═══════════════════════════════`,
                `📋 ${data.verdict}`,
            ].filter(Boolean).join("\n");
            window.alert(lines);
        } catch (e) {
            toast.error(errMsg(e, "فشل forensic debug"));
        }
    };


    const runTamaraAudit = async () => {
        try {
            toast.info("جاري إعداد تقرير التدقيق…", { duration: 2000 });
            const { data } = await api.get(`/bnpl/tamara/audit`);
            console.log("Tamara audit →", data);
            const s = data.summary || {};
            const bs = data.by_status_key_hints || {};
            const api_amounts = data.amounts_from_api || {};
            const uo = data.amounts_in_unified_orders || {};
            const d = data.delta || {};
            const dups = (data.sample_duplicate_orders || [])
                .slice(0, 5)
                .map((r) => `  ${r.order_reference_id}: ${r.transactions} txns [${(r.statuses || []).join(", ")}]`)
                .join("\n");
            const fmt = (n) => Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            const rc = data.refunds_comparison || {};
            const lines = [
                `🔍 TAMARA AUDIT REPORT`,
                ``,
                `═══ 1) Counts ═══`,
                `Total transactions synced:  ${s.total_transactions_synced}`,
                `Unique orders:              ${s.unique_orders}`,
                `Orders w/ multi tx:         ${s.orders_with_multi_transactions}`,
                `Orphan tx (no ref):         ${s.orphan_transactions_no_ref}`,
                ``,
                `═══ 2-4) By Status ═══`,
                `Authorised:           ${bs.authorised}`,
                `Fully Captured:       ${bs.fully_captured}`,
                `Partially Captured:   ${bs.partially_captured}`,
                `Fully Refunded:       ${bs.fully_refunded}`,
                `Partially Refunded:   ${bs.partially_refunded}`,
                `Canceled:             ${bs.canceled}`,
                ``,
                `═══ 4b) Refunds Comparison ═══`,
                `Orders w/ refund status (Fully + Partial): ${rc.orders_with_refund_status}`,
                `payment_refunds records:                    ${rc.payment_refunds_records}`,
                `ptx with refunded_amount > 0:                ${rc.payment_transactions_with_refunded_gt_0}`,
                `Refunded amount in transactions:             ${fmt(rc.refunded_amount_in_transactions)}`,
                `Refunded amount in refund records:           ${fmt(rc.refunded_amount_in_refund_records)}`,
                `Delta (status vs records):                   ${rc.delta_records_vs_status}`,
                ``,
                `${rc.diagnosis || ""}`,
                ``,
                `═══ 5) Sample of orders w/ multiple transactions ═══`,
                dups || `  (none — every order has exactly 1 txn ✓)`,
                ``,
                `═══ 6-7) Amount totals ═══`,
                `API total (Σ amount):      ${fmt(api_amounts.amount)}`,
                `API captured (Σ captured): ${fmt(api_amounts.captured)}`,
                `API refunded (Σ refunded): ${fmt(api_amounts.refunded)}`,
                ``,
                `Unified orders (matched):  ${uo.count} rows`,
                `  gross_amount:            ${fmt(uo.gross)}`,
                `  paid_amount:             ${fmt(uo.paid)}`,
                `  refunded_amount:         ${fmt(uo.refunded)}`,
                ``,
                `═══ 8) Delta (API − Unified) ═══`,
                `amount − gross:        ${fmt(d.amount_vs_gross)}   ${d.amount_vs_gross === 0 ? "✓" : "⚠"}`,
                `captured − paid:       ${fmt(d.captured_vs_paid)}   ${d.captured_vs_paid === 0 ? "✓" : "⚠"}`,
                `refunded − refunded:   ${fmt(d.refunded_vs_refunded)}   ${d.refunded_vs_refunded === 0 ? "✓" : "⚠"}`,
                ``,
                `📋 VERDICT: ${data.verdict}`,
            ].join("\n");
            window.alert(lines);
        } catch (e) {
            toast.error(errMsg(e, "فشل تقرير التدقيق"));
        }
    };


    const runTabbyDebug = async () => {
        try {
            toast.info("جاري تشخيص Tabby…", { duration: 2000 });
            const { data } = await api.post(`/bnpl/tabby/debug`);
            console.log("Tabby debug report →", data);
            const wd = data.with_date_filter || {};
            const byStatus = Object.entries(data.by_status || {})
                .map(([k, v]) => `  ${k}: ${v}`).join("\n");
            const sampleDates = (data.sample_payments || [])
                .map((p) => `  ${(p.created_at || "").slice(0, 19)} · ${p.status} · ${p.amount}`)
                .join("\n");
            const lines = [
                `🔍 TABBY DEBUG REPORT`,
                ``,
                `🔑 Key type:  ${data.key_type?.toUpperCase() || "?"}  (${data.key_masked || ""})`,
                `🏪 Merchant code:  ${data.merchant_code || "(none)"}`,
                `🌐 Endpoint:  ${data.endpoint}`,
                ``,
                `── A) Without any filter ──`,
                `Raw returned: ${data.raw_payments_count ?? "?"}`,
                `Total count (Tabby): ${data.pagination_total_count ?? "?"}`,
                sampleDates ? `\nLast 10 payments:\n${sampleDates}` : "",
                ``,
                `── B) With activation_date filter (${data.activation_date_in_settings || "?"}) ──`,
                wd.error
                    ? `❌ Error: ${wd.error}`
                    : `Returned: ${wd.raw_count ?? "?"} · ` +
                      `Total: ${wd.pagination_total_count ?? "?"}\n` +
                      (wd.sample_dates ? `Sample dates:\n  ${wd.sample_dates.join("\n  ")}` : ""),
                ``,
                `── C) Total per Tabby status ──`,
                byStatus,
                ``,
                `📋 DIAGNOSIS:`,
                data.diagnosis || "—",
            ].join("\n");
            window.alert(lines);
            await load();
        } catch (e) {
            toast.error(errMsg(e, "فشل تشخيص Tabby"));
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
                        <ArrowsClockwise size={14} /> جلب تاريخي Tabby (تلقائي)
                    </button>
                    <button
                        type="button"
                        onClick={startTabbyBackfill}
                        disabled={tabbyBusy}
                        className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-bold rounded-lg hover:bg-emerald-700 disabled:opacity-50 flex items-center gap-1"
                        data-testid="bnpl-diag-tabby-start"
                    >
                        ▶ Start Tabby Backfill
                    </button>
                    <button
                        type="button"
                        onClick={continueTabbyBackfill}
                        disabled={tabbyBusy || !tabbyJob?.job_id || tabbyJob?.status === "done"}
                        className="px-3 py-1.5 bg-sky-700 text-white text-xs font-bold rounded-lg hover:bg-sky-800 disabled:opacity-50 flex items-center gap-1"
                        data-testid="bnpl-diag-tabby-continue"
                    >
                        ⏭ Continue Backfill
                    </button>
                    <button
                        type="button"
                        onClick={runTabbyDebug}
                        className="px-3 py-1.5 bg-fuchsia-700 text-white text-xs font-bold rounded-lg hover:bg-fuchsia-800 flex items-center gap-1"
                        data-testid="bnpl-diag-tabby-debug"
                    >
                        <Stethoscope size={14} /> Tabby Debug
                    </button>
                    <button
                        type="button"
                        onClick={runTabbySyncDebug}
                        className="px-3 py-1.5 bg-pink-700 text-white text-xs font-bold rounded-lg hover:bg-pink-800 flex items-center gap-1"
                        data-testid="bnpl-diag-tabby-sync-debug"
                    >
                        <Stethoscope size={14} /> Tabby Forensic
                    </button>
                    <button
                        type="button"
                        onClick={runTamaraBackfill}
                        className="px-3 py-1.5 bg-purple-600 text-white text-xs font-bold rounded-lg hover:bg-purple-700 flex items-center gap-1"
                        data-testid="bnpl-diag-tamara-backfill"
                    >
                        <ArrowsClockwise size={14} /> فحص دفعة Tamara
                    </button>
                    <button
                        type="button"
                        onClick={runTamaraFullBackfill}
                        className="px-3 py-1.5 bg-rose-700 text-white text-xs font-bold rounded-lg hover:bg-rose-800 flex items-center gap-1"
                        data-testid="bnpl-diag-tamara-backfill-full"
                    >
                        <ArrowsClockwise size={14} /> فحص كامل Tamara
                    </button>
                    <button
                        type="button"
                        onClick={runTamaraAudit}
                        className="px-3 py-1.5 bg-emerald-700 text-white text-xs font-bold rounded-lg hover:bg-emerald-800 flex items-center gap-1"
                        data-testid="bnpl-diag-tamara-audit"
                    >
                        <Stethoscope size={14} /> تدقيق Tamara
                    </button>
                    <button
                        type="button"
                        onClick={runTamaraRefundInspect}
                        className="px-3 py-1.5 bg-orange-600 text-white text-xs font-bold rounded-lg hover:bg-orange-700 flex items-center gap-1"
                        data-testid="bnpl-diag-tamara-refund-inspect"
                    >
                        <Stethoscope size={14} /> فحص Refund Tamara
                    </button>
                    <button
                        type="button"
                        onClick={runRebuildRefunds}
                        className="px-3 py-1.5 bg-teal-700 text-white text-xs font-bold rounded-lg hover:bg-teal-800 flex items-center gap-1"
                        data-testid="bnpl-diag-rebuild-refunds"
                    >
                        <ArrowsClockwise size={14} /> إعادة بناء Refunds
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

            {/* Tabby Backfill — Live Job Status (Iter-116) */}
            {tabbyJob && (
                <div
                    className="rounded-2xl border-2 border-sky-300 bg-sky-50 p-5"
                    data-testid="bnpl-diag-tabby-job-panel"
                >
                    <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                        <h2 className="text-lg font-extrabold text-slate-900">
                            🔁 Tabby Backfill — حالة الـ Job
                        </h2>
                        <div className="flex items-center gap-2 text-xs">
                            <span className="text-slate-600">
                                Job ID:{" "}
                                <code className="bg-white px-2 py-0.5 rounded border border-slate-200 num">
                                    {tabbyJob.job_id?.slice(0, 12)}…
                                </code>
                            </span>
                            <span
                                className={
                                    "px-2 py-0.5 rounded-full font-bold " +
                                    (tabbyJob.status === "done"
                                        ? "bg-emerald-600 text-white"
                                        : tabbyJob.status === "error"
                                            ? "bg-rose-600 text-white"
                                            : "bg-amber-500 text-white")
                                }
                                data-testid="bnpl-diag-tabby-job-status"
                            >
                                {tabbyJob.status}
                            </span>
                        </div>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
                        <StatTile
                            label="pages_read"
                            value={Number(tabbyJob.pages_read || 0).toLocaleString("en-US")}
                            tone="sky"
                            testid="bnpl-diag-tabby-job-pages"
                        />
                        <StatTile
                            label="total_fetched"
                            value={Number(tabbyJob.total_fetched || 0).toLocaleString("en-US")}
                            tone="sky"
                            testid="bnpl-diag-tabby-job-fetched"
                        />
                        <StatTile
                            label="total_saved"
                            value={Number(tabbyJob.total_saved || 0).toLocaleString("en-US")}
                            tone={tabbyJob.total_saved > 0 ? "emerald" : "amber"}
                            testid="bnpl-diag-tabby-job-saved"
                        />
                        <StatTile
                            label="skipped_old (قبل cutoff)"
                            value={Number(tabbyJob.skipped_old || 0).toLocaleString("en-US")}
                            tone="amber"
                            testid="bnpl-diag-tabby-job-skipped"
                        />
                        <StatTile
                            label="oldest_date_seen"
                            value={tabbyJob.oldest_date_seen || "—"}
                            tone="slate"
                            testid="bnpl-diag-tabby-job-oldest"
                        />
                        <StatTile
                            label="newest_date_seen"
                            value={tabbyJob.newest_date_seen || "—"}
                            tone="slate"
                            testid="bnpl-diag-tabby-job-newest"
                        />
                        <StatTile
                            label="stop_reason"
                            value={tabbyJob.stop_reason || "—"}
                            tone={
                                tabbyJob.stop_reason === "empty_page" ||
                                    tabbyJob.stop_reason === "short_page"
                                    ? "emerald"
                                    : "amber"
                            }
                            hint={`last_offset=${tabbyJob.last_offset || 0}`}
                            testid="bnpl-diag-tabby-job-stop"
                        />
                    </div>
                    {tabbyJob.status !== "done" && (
                        <div className="mt-3 text-xs text-slate-700 bg-white border border-slate-200 rounded-lg p-3 flex items-center gap-2">
                            <Warning size={16} className="text-amber-600" />
                            الـ Job لم يكتمل بعد — اضغط زر{" "}
                            <strong className="text-sky-800">Continue Backfill</strong> لمتابعة الدفعة التالية
                            (كل دفعة = ~100 معاملة). كرّر حتى تصبح <code>status = done</code> و{" "}
                            <code>stop_reason = empty_page</code> أو <code>short_page</code>.
                        </div>
                    )}
                </div>
            )}

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
