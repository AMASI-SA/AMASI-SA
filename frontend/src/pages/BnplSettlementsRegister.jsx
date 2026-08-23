/**
 * Iter-221 — تسجيل تسويات Tabby و Tamara و Emkan (Phase 2b UI).
 *
 * صفحة مخصصة لمراجعة وتسجيل تسويات مزوّدي BNPL عبر:
 *     POST /api/bnpl/settlements/register
 *
 * تعتمد كذلك على:
 *     GET /api/bnpl/settlements/registration-overview
 *     GET /api/bnpl/settlements/registered
 *     GET /api/bnpl/settlements/registered/{txn_group_id}
 *     GET /api/accounts                  (لاختيار البنك المستلم)
 *
 * المخرجات تنعكس فوراً في:
 *   - المركز المالي (FinancialPosition.jsx)
 *   - الأصول والحسابات (Accounts.jsx)
 *   - سجل الحركات المالية (LedgerTransactionsPage.jsx)
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
    e?.response?.data?.detail
    || e?.response?.data?.error
    || e?.message
    || fb;

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

// Iter-246z — BNPL Timezone SSOT. Saudi Arabia is Asia/Riyadh
// (AST = UTC+3, no DST). Every BNPL-related date default MUST go
// through these helpers so the modal never silently slips a calendar
// day between 00:00 and 03:00 Riyadh time.  `todayISO()` (UTC) was
// removed under Iter-246z — do not re-introduce it.
const todayRiyadhISO = () => {
    const now = new Date();
    const r = new Date(now.getTime() + 3 * 3600 * 1000);
    return r.toISOString().slice(0, 10);
};
const nowRiyadh = () => {
    const now = new Date();
    return new Date(now.getTime() + 3 * 3600 * 1000);
};

const PROVIDERS = {
    tabby:  { name: "Tabby",  badge: "🟣", color: "violet" },
    tamara: { name: "Tamara", badge: "🩷", color: "rose" },
    emkan:  { name: "إمكان",  badge: "🟡", color: "amber" },
};

const STATUS_COLORS = {
    green:  { bg: "bg-emerald-50", border: "border-emerald-200",
              text: "text-emerald-700", label: "مطابق" },
    yellow: { bg: "bg-amber-50",   border: "border-amber-200",
              text: "text-amber-700", label: "فرق بسيط" },
    red:    { bg: "bg-rose-50",    border: "border-rose-200",
              text: "text-rose-700", label: "يحتاج مراجعة" },
};


function StatusPill({ status }) {
    const c = STATUS_COLORS[status] || STATUS_COLORS.green;
    return (
        <span
            data-testid={`match-status-${status}`}
            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full
                        text-[11px] font-bold ${c.bg} ${c.border} ${c.text}
                        border`}
        >
            <span className="w-1.5 h-1.5 rounded-full bg-current"></span>
            {c.label}
        </span>
    );
}


function MetricCell({ label, value, tone = "slate", testid }) {
    const toneCls = {
        slate:   "text-slate-900",
        emerald: "text-emerald-700",
        rose:    "text-rose-700",
        amber:   "text-amber-700",
        violet:  "text-violet-700",
    }[tone];
    return (
        <div className="flex flex-col gap-0.5" data-testid={testid}>
            <div className="text-[10px] text-slate-500 font-semibold">
                {label}
            </div>
            <div className={`num text-base font-extrabold ${toneCls}`}>
                {value}
            </div>
        </div>
    );
}


function ProviderOverviewCard({ row, onOpenAdd, onOpenAuto }) {
    const meta = PROVIDERS[row.provider] || { name: row.provider, badge: "💳" };
    const last = row.last_settlement;
    const diffTone =
        Math.abs(row.difference) < 0.5 ? "emerald"
        : row.match_status === "yellow" ? "amber"
        : "rose";
    return (
        <div
            className="rounded-2xl border border-slate-200 bg-white p-5
                       shadow-sm hover:shadow transition-shadow"
            data-testid={`provider-card-${row.provider}`}
        >
            <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                    <h3 className="text-xl font-extrabold text-slate-900
                                   flex items-center gap-2">
                        <span className="text-2xl">{meta.badge}</span>
                        {meta.name}
                    </h3>
                    <div className="mt-1">
                        <StatusPill status={row.match_status} />
                    </div>
                </div>
                <div className="flex gap-1.5">
                    <button
                        type="button"
                        onClick={() => onOpenAuto(row.provider)}
                        className="px-3 py-1.5 bg-violet-700 hover:bg-violet-800
                                   text-white text-xs font-bold rounded-lg
                                   flex items-center gap-1.5"
                        data-testid={`auto-import-btn-${row.provider}`}
                        title={row.provider === "emkan"
                            ? "جلب من ملف التسوية المرفوع ثم اعتماد"
                            : "جلب من API ثم اعتماد"}
                    >
                        <span className="text-base">📥</span>
                        {row.provider === "emkan" ? "جلب من الملف" : "جلب تلقائي"}
                    </button>
                    <button
                        type="button"
                        onClick={() => onOpenAdd(row.provider)}
                        className="px-3 py-1.5 bg-slate-900 hover:bg-slate-800
                                   text-white text-xs font-bold rounded-lg
                                   flex items-center gap-1.5"
                        data-testid={`add-settlement-btn-${row.provider}`}
                    >
                        <span className="text-base">＋</span>
                        إضافة يدوياً
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-3">
                <MetricCell
                    label="الذمم الحالية"
                    value={`${fmt(row.current_receivable)} ر.س`}
                    tone={row.current_receivable > 0 ? "violet" : "slate"}
                    testid={`metric-current-receivable-${row.provider}`}
                />
                <MetricCell
                    label="المتوقع استلامه"
                    value={`${fmt(row.expected_total)} ر.س`}
                    testid={`metric-expected-${row.provider}`}
                />
                <MetricCell
                    label="تم استلامه"
                    value={`${fmt(row.received_total)} ر.س`}
                    tone={row.received_total > 0 ? "emerald" : "slate"}
                    testid={`metric-received-${row.provider}`}
                />
                <MetricCell
                    label="الفرق"
                    value={`${row.difference >= 0 ? "" : "−"}${fmt(Math.abs(row.difference))} ر.س`}
                    tone={diffTone}
                    testid={`metric-diff-${row.provider}`}
                />
            </div>

            <div className="bg-slate-50 rounded-xl p-3 border border-slate-100">
                <div className="text-[10px] font-bold text-slate-500 mb-1">
                    آخر تسوية مسجَّلة
                </div>
                {last ? (
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1
                                    text-xs text-slate-700">
                        <span className="font-bold num">
                            {fmt(last.amount)} ر.س
                        </span>
                        <span>·</span>
                        <span className="text-slate-500">
                            مرجع: <span className="font-mono text-slate-700">
                                {last.reference || "—"}
                            </span>
                        </span>
                        {last.settlement_date && (
                            <>
                                <span>·</span>
                                <span className="text-slate-500 num">
                                    {last.settlement_date}
                                </span>
                            </>
                        )}
                        {last.bank_account_name && (
                            <>
                                <span>·</span>
                                <span className="text-slate-500">
                                    {last.bank_account_name}
                                </span>
                            </>
                        )}
                    </div>
                ) : (
                    <div className="text-xs text-slate-400">
                        لا توجد تسويات مسجَّلة بعد لهذا المزوّد.
                    </div>
                )}
            </div>
        </div>
    );
}


function AddSettlementModal({ provider, banks, prefill, onClose, onSaved }) {
    const meta = PROVIDERS[provider] || { name: provider, badge: "💳" };
    const [saving, setSaving] = useState(false);
    const [importing, setImporting] = useState(false);
    const [importMeta, setImportMeta] = useState(null);
    const [importPeriod, setImportPeriod] = useState("last_week");

    // Iter-246x — provider invoice-issuance weekday (Asia/Riyadh).
    // 0=Mon … 5=Sat. Modal forbids "حفظ" before this weekday for the
    // selected period, matching the backend guard.
    const INVOICE_WD = { tamara: 5, tabby: 0 };
    const WD_AR = ["الإثنين","الثلاثاء","الأربعاء","الخميس",
                   "الجمعة","السبت","الأحد"];
    const earliestSaveISO = (() => {
        const pt = importMeta?.period?.to;
        if (!pt || !(provider in INVOICE_WD)) return null;
        const [y, m, d] = pt.split("-").map(Number);
        const first = new Date(Date.UTC(y, m - 1, d));
        first.setUTCDate(first.getUTCDate() + 1);
        const wd = INVOICE_WD[provider] ?? 5;
        // JS getUTCDay: 0=Sun … 6=Sat. Convert to Mon=0…Sun=6.
        const jsToMon0 = (j) => (j + 6) % 7;
        let delta = (wd - jsToMon0(first.getUTCDay()) + 7) % 7;
        first.setUTCDate(first.getUTCDate() + delta);
        return first.toISOString().slice(0, 10);
    })();
    const todayRiyadh = (() => {
        const now = new Date();
        const r = new Date(now.getTime() + 3 * 3600 * 1000);
        return r.toISOString().slice(0, 10);
    })();
    const beforeInvoiceDay = !!(earliestSaveISO
                                && todayRiyadh < earliestSaveISO);

    const [form, setForm] = useState({
        settlement_reference: prefill?.settlement_reference || "",
        // Iter-246z — default settlement_date is Asia/Riyadh today,
        // NOT the browser's UTC date (which slips back a day between
        // midnight and 03:00 Riyadh time).
        settlement_date: prefill?.settlement_date || todayRiyadhISO(),
        bank_account_id: prefill?.bank_account_id || banks?.[0]?.id || "",
        transferred_amount: prefill?.transferred_amount ?? "",
        commission: prefill?.commission ?? "",
        commission_vat: prefill?.commission_vat ?? "",
        settlement_fee: prefill?.settlement_fee ?? "",
        notes: prefill?.notes || "",
    });

    const total = useMemo(() => {
        return [
            form.transferred_amount, form.commission,
            form.commission_vat, form.settlement_fee,
        ].reduce((s, v) => s + (parseFloat(v) || 0), 0);
    }, [form]);

    const set = (k) => (e) =>
        setForm((f) => ({ ...f, [k]: e.target.value }));

    const runImport = async () => {
        setImporting(true);
        try {
            const { data } = await api.get(
                `/bnpl/settlements/import-preview/${provider}`,
                { params: { period: importPeriod } },
            );
            const pf = data?.prefill || {};
            setForm((f) => ({
                ...f,
                settlement_reference: pf.settlement_reference || f.settlement_reference,
                settlement_date: pf.settlement_date || f.settlement_date,
                bank_account_id: pf.bank_account_id || f.bank_account_id,
                transferred_amount: pf.transferred_amount ?? 0,
                commission: pf.commission ?? 0,
                commission_vat: pf.commission_vat ?? 0,
                settlement_fee: pf.settlement_fee ?? 0,
                notes: pf.notes || f.notes,
            }));
            setImportMeta({
                period: data?.period,
                data_source: data?.data_source,
                breakdown: data?.breakdown,
            });
            toast.success("تم جلب القيم. راجع ثم اعتمد للحفظ.");
        } catch (e) {
            toast.error(errMsg(e, "فشل جلب بيانات التسوية"));
        } finally {
            setImporting(false);
        }
    };

    // Iter-225 — Force re-sync the provider for the chosen period so a
    // late/missing refund (or sale) is captured before re-running the
    // import. Maps the period dropdown → a `since=YYYY-MM-DD` date.
    const [resyncing, setResyncing] = useState(false);
    const runResync = async () => {
        if (provider !== "tabby" && provider !== "tamara") return;
        setResyncing(true);
        try {
            // Iter-246z — Use Asia/Riyadh "today" for week boundaries.
            const today = nowRiyadh();
            const ms = 86400000;
            const fmt = (d) => d.toISOString().slice(0, 10);
            let since;
            if (importPeriod === "last_week") {
                const wd = today.getUTCDay() || 7;
                since = fmt(new Date(today.getTime() - (wd + 6) * ms));
            } else if (importPeriod === "this_week") {
                const wd = today.getUTCDay() || 7;
                since = fmt(new Date(today.getTime() - (wd - 1) * ms));
            } else if (importPeriod === "last_7d") {
                since = fmt(new Date(today.getTime() - 6 * ms));
            } else if (importPeriod === "last_14d") {
                since = fmt(new Date(today.getTime() - 13 * ms));
            } else if (importPeriod === "this_month") {
                since = fmt(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1)));
            } else if (importPeriod === "last_month") {
                since = fmt(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1)));
            }

            const endpoint = provider === "tabby"
                ? "/bnpl/tabby/sync"
                : "/bnpl/tamara/backfill";
            const { data } = await api.post(endpoint, null, {
                params: { since },
            });
            const upserts = (data?.transactions_upserted || 0) +
                            (data?.refunds_upserted || 0) +
                            (data?.orders_processed || 0);
            toast.success(
                `تمت إعادة المزامنة (since=${since}). ` +
                `محدَّث: ${upserts} عملية. اضغط "جلب وملء" لإعادة الاحتساب.`,
            );
        } catch (e) {
            toast.error(errMsg(e, "فشل إعادة المزامنة"));
        } finally {
            setResyncing(false);
        }
    };

    const save = async () => {
        if (!form.settlement_reference.trim()) {
            toast.error("رقم التسوية مطلوب");
            return;
        }
        if (!form.bank_account_id) {
            toast.error("اختر البنك المستلم");
            return;
        }
        if (total <= 0) {
            toast.error("يجب إدخال مبلغ تسوية موجب");
            return;
        }
        setSaving(true);
        try {
            const payload = {
                provider,
                bank_account_id: form.bank_account_id,
                transferred_amount: parseFloat(form.transferred_amount) || 0,
                commission: parseFloat(form.commission) || 0,
                commission_vat: parseFloat(form.commission_vat) || 0,
                settlement_fee: parseFloat(form.settlement_fee) || 0,
                settlement_reference: form.settlement_reference.trim(),
                settlement_date: form.settlement_date || null,
                notes: form.notes || "",
                // Iter-246x — forward the period so the backend's
                // duplicate guard + invoice-day guard can fire.
                period_from: importMeta?.period?.from || null,
                period_to: importMeta?.period?.to || null,
            };
            const { data } = await api.post(
                "/bnpl/settlements/register", payload,
            );
            if (data?.skipped) {
                toast.info("تم تسجيل هذه التسوية مسبقاً — لم يُنشأ قيد جديد.");
            } else {
                toast.success("تم تسجيل التسوية وإنشاء القيد بنجاح.");
            }
            onSaved(data.txn_group_id);
        } catch (e) {
            toast.error(errMsg(e, "فشل تسجيل التسوية"));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div
            className="fixed inset-0 bg-black/40 z-50 flex items-center
                       justify-center p-4"
            onClick={onClose}
            data-testid="add-settlement-modal"
        >
            <div
                className="bg-white rounded-2xl shadow-2xl w-full max-w-xl
                           max-h-[90vh] overflow-y-auto"
                onClick={(e) => e.stopPropagation()}
                dir="rtl"
            >
                <div className="p-5 border-b border-slate-200 sticky top-0
                                bg-white z-10">
                    <h2 className="text-lg font-extrabold text-slate-900
                                   flex items-center gap-2">
                        <span className="text-2xl">{meta.badge}</span>
                        إضافة تسوية {meta.name}
                    </h2>
                    <p className="text-xs text-slate-500 mt-1">
                        ستنشئ هذه العملية قيداً متوازناً في الدفتر العام.
                    </p>
                    {/* Iter-246z — Asia/Riyadh SSOT banner */}
                    <div className="mt-2 inline-flex items-center gap-1.5
                                    px-2 py-1 rounded-md bg-emerald-50
                                    border border-emerald-200 text-[10px]
                                    font-bold text-emerald-800"
                         data-testid="riyadh-tz-banner">
                        <span>🇸🇦</span>
                        <span>
                            جميع فترات وتسويات BNPL تعتمد توقيت الرياض
                            (Asia/Riyadh)
                        </span>
                    </div>
                </div>

                {/* Iter-223 — Auto Import bar */}
                <div className="px-5 pt-4 pb-3 bg-violet-50/40
                                border-b border-violet-100">
                    <div className="flex items-center justify-between
                                    gap-2 flex-wrap">
                        <div className="flex items-center gap-2 text-xs
                                        font-bold text-violet-900">
                            <span>
                                📥 {provider === "emkan"
                                    ? "جلب من ملفات التسوية المرفوعة"
                                    : "جلب تلقائي من API"}
                            </span>
                            <select
                                value={importPeriod}
                                onChange={(e) => setImportPeriod(e.target.value)}
                                className="px-2 py-1 rounded-md border
                                           border-violet-200 bg-white
                                           text-[11px] text-slate-700 font-bold"
                                data-testid="import-period"
                            >
                                <option value="last_week">الأسبوع الماضي</option>
                                <option value="this_week">هذا الأسبوع</option>
                                <option value="last_7d">آخر 7 أيام</option>
                                <option value="last_14d">آخر 14 يوماً</option>
                                <option value="this_month">هذا الشهر</option>
                                <option value="last_month">الشهر الماضي</option>
                            </select>
                        </div>
                        <div className="flex gap-1.5">
                            {provider !== "emkan" && <button
                                type="button"
                                onClick={runResync}
                                disabled={resyncing || importing}
                                className="px-3 py-1.5 rounded-lg
                                           border border-violet-300
                                           bg-white hover:bg-violet-50
                                           text-violet-700 text-[11px]
                                           font-bold disabled:opacity-60"
                                data-testid="btn-resync-provider"
                                title={`إعادة مزامنة ${meta.name} للفترة المختارة قبل الجلب`}
                            >
                                {resyncing
                                    ? "جارٍ المزامنة…"
                                    : `🔄 إعادة مزامنة ${meta.name}`}
                            </button>}
                            <button
                                type="button"
                                onClick={runImport}
                                disabled={importing || resyncing}
                                className="px-3 py-1.5 rounded-lg bg-violet-700
                                           hover:bg-violet-800 text-white
                                           text-[11px] font-bold disabled:opacity-60"
                                data-testid="btn-auto-import"
                            >
                                {importing
                                    ? "جارٍ الجلب…"
                                    : "جلب وملء الحقول تلقائياً"}
                            </button>
                        </div>
                    </div>
                    {importMeta && (
                        <div className="mt-2 grid grid-cols-2 sm:grid-cols-6
                                        gap-2 text-[10px] text-slate-700
                                        bg-white rounded-lg p-2
                                        border border-violet-100"
                             data-testid="import-summary">
                            <div>
                                <span className="font-bold">المصدر:</span>{" "}
                                <span className="font-mono">
                                    {importMeta.data_source}
                                </span>
                            </div>
                            <div>
                                <span className="font-bold">إجمالي المبيعات:</span>{" "}
                                <span className="num">
                                    {fmt(importMeta.breakdown?.gross_sales)}
                                </span>
                            </div>
                            <div>
                                <span className="font-bold">المرتجعات:</span>{" "}
                                <span className="num">
                                    {fmt(importMeta.breakdown?.total_refunds)}
                                </span>
                            </div>
                            <div>
                                <span className="font-bold">صافي المبيعات:</span>{" "}
                                <span className="num">
                                    {fmt(importMeta.breakdown?.net_sales)}
                                </span>
                            </div>
                            <div>
                                <span className="font-bold">الفترة:</span>{" "}
                                <span className="num">
                                    {importMeta.period?.from} → {importMeta.period?.to}
                                </span>
                            </div>
                            {/* Iter-246s — Engine version badge. Proves to
                                the merchant that the iter246r forensic
                                hardening is active. Renders RED if not. */}
                            <div data-testid="engine-version-badge">
                                <span className="font-bold">المحرّك:</span>{" "}
                                <span
                                    className={
                                        "font-mono px-1.5 py-0.5 rounded " +
                                        (importMeta.breakdown?.engine_version
                                         === "iter246r"
                                            ? "bg-emerald-100 text-emerald-800"
                                            : "bg-rose-100 text-rose-800 font-bold")
                                    }
                                >
                                    {importMeta.breakdown?.engine_version
                                     || "unknown"}
                                </span>
                            </div>
                        </div>
                    )}
                    {/* Iter-246s — SSOT safety guard: block save when the
                        backend is running an older engine (no Tamara
                        historical-pin + Net-Zero filters). Prevents the
                        merchant from posting an inflated Gross to the
                        general ledger. */}
                    {importMeta
                     && provider === "tamara"
                     && importMeta.breakdown?.engine_version !== "iter246r" && (
                        <div className="mt-2 p-2 rounded-lg bg-rose-50
                                        border border-rose-300 text-[11px]
                                        text-rose-800 font-bold"
                             data-testid="engine-version-warning">
                            ⚠️ تحذير SSOT: المحرّك الحالي
                            ({importMeta.breakdown?.engine_version || "unknown"})
                            ليس <span className="font-mono">iter246r</span>.
                            قد تكون أرقام إجمالي المبيعات مُضخّمة بسبب
                            استرداد قيود تاريخية. زر &quot;حفظ وإنشاء القيد&quot;
                            معطّل لمنع ترحيل أرقام خاطئة إلى دفتر الأستاذ.
                            يرجى التأكد من نشر آخر إصدار من الـ backend.
                        </div>
                    )}
                    {/* Iter-246x — invoice-day guard (Tamara=Sat, Tabby=Mon). */}
                    {importMeta && beforeInvoiceDay && (
                        <div className="mt-2 p-2 rounded-lg bg-amber-50
                                        border border-amber-300 text-[11px]
                                        text-amber-900 font-bold"
                             data-testid="invoice-day-warning">
                            ⏳ لا يمكن إنشاء تسوية هذا الأسبوع قبل صدور
                            فاتورة المزود الرسمية. تمارا تصدر يوم السبت،
                            وتابي تصدر يوم الاثنين.
                            <div className="mt-1 font-mono text-[10px]">
                                {meta.name} → التاريخ المتاح للتسجيل:{" "}
                                <span className="font-bold">
                                    {earliestSaveISO}
                                </span>{" "}
                                ({WD_AR[INVOICE_WD[provider] ?? 5]})
                            </div>
                            <div className="text-[10px] mt-1">
                                المعاينة مسموحة الآن. زر &quot;حفظ وإنشاء
                                القيد&quot; سيتفعّل تلقائياً بعد التاريخ
                                المذكور.
                            </div>
                        </div>
                    )}
                </div>

                <div className="p-5 space-y-4">
                    <FormField label="رقم التسوية / المرجع" required>
                        <input
                            type="text"
                            value={form.settlement_reference}
                            onChange={set("settlement_reference")}
                            placeholder="مثال: TBY-W42-001"
                            className="w-full px-3 py-2 rounded-lg border
                                       border-slate-300 text-sm font-mono
                                       focus:outline-none focus:ring-2
                                       focus:ring-slate-900/10"
                            data-testid="input-settlement-reference"
                        />
                    </FormField>

                    <div className="grid grid-cols-2 gap-3">
                        <FormField label="تاريخ التسوية">
                            <input
                                type="date"
                                value={form.settlement_date}
                                onChange={set("settlement_date")}
                                className="w-full px-3 py-2 rounded-lg border
                                           border-slate-300 text-sm num"
                                data-testid="input-settlement-date"
                            />
                        </FormField>
                        <FormField label="البنك / الصندوق المستلم" required>
                            <select
                                value={form.bank_account_id}
                                onChange={set("bank_account_id")}
                                className="w-full px-3 py-2 rounded-lg border
                                           border-slate-300 text-sm bg-white"
                                data-testid="input-bank-account"
                            >
                                <option value="">— اختر —</option>
                                {(banks || []).map((b) => (
                                    <option key={b.id} value={b.id}>
                                        {b.name}
                                        {b.account_type === "cash"
                                            ? " (صندوق)"
                                            : ""}
                                    </option>
                                ))}
                            </select>
                        </FormField>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <FormField label="المبلغ المحوَّل">
                            <NumInput
                                value={form.transferred_amount}
                                onChange={set("transferred_amount")}
                                placeholder="0.00"
                                testid="input-transferred-amount"
                            />
                        </FormField>
                        <FormField label="العمولة">
                            <NumInput
                                value={form.commission}
                                onChange={set("commission")}
                                placeholder="0.00"
                                testid="input-commission"
                            />
                        </FormField>
                        <FormField label="ضريبة العمولة (VAT)">
                            <NumInput
                                value={form.commission_vat}
                                onChange={set("commission_vat")}
                                placeholder="0.00"
                                testid="input-commission-vat"
                            />
                        </FormField>
                        <FormField label="رسوم التسوية">
                            <NumInput
                                value={form.settlement_fee}
                                onChange={set("settlement_fee")}
                                placeholder="0.00"
                                testid="input-settlement-fee"
                            />
                        </FormField>
                    </div>

                    <FormField label="ملاحظات (اختياري)">
                        <textarea
                            rows={2}
                            value={form.notes}
                            onChange={set("notes")}
                            className="w-full px-3 py-2 rounded-lg border
                                       border-slate-300 text-sm"
                            data-testid="input-notes"
                        />
                    </FormField>

                    <div className="bg-slate-50 rounded-xl border
                                    border-slate-200 p-3 flex items-baseline
                                    justify-between">
                        <span className="text-xs font-bold text-slate-600">
                            إجمالي ما سيُغلق من الذمم
                        </span>
                        <span className="num text-lg font-extrabold
                                         text-slate-900"
                              data-testid="modal-total">
                            {fmt(total)} ر.س
                        </span>
                    </div>
                </div>

                <div className="p-4 border-t border-slate-200 flex justify-end
                                gap-2 sticky bottom-0 bg-white">
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg border
                                   border-slate-300 text-sm font-bold
                                   text-slate-700 hover:bg-slate-50"
                        data-testid="modal-cancel"
                    >
                        إلغاء
                    </button>
                    <button
                        type="button"
                        onClick={save}
                        disabled={
                            saving
                            || beforeInvoiceDay
                            || (provider === "tamara"
                                && importMeta
                                && importMeta.breakdown?.engine_version
                                   !== "iter246r")
                        }
                        title={
                            beforeInvoiceDay
                                ? `الحفظ متاح ابتداءً من ${earliestSaveISO}`
                                : (provider === "tamara"
                                   && importMeta
                                   && importMeta.breakdown?.engine_version
                                      !== "iter246r")
                                    ? "محرّك الحساب ليس iter246r — الحفظ معطّل لمنع ترحيل أرقام مُضخّمة"
                                    : ""
                        }
                        className="px-4 py-2 rounded-lg bg-slate-900
                                   hover:bg-slate-800 text-white text-sm
                                   font-bold disabled:opacity-60
                                   disabled:cursor-not-allowed"
                        data-testid="modal-save"
                    >
                        {saving ? "جارٍ الحفظ…" : "حفظ وإنشاء القيد"}
                    </button>
                </div>
            </div>
        </div>
    );
}


function FormField({ label, children, required }) {
    return (
        <label className="block">
            <span className="text-[11px] font-bold text-slate-600 mb-1
                             inline-block">
                {label}
                {required && <span className="text-rose-600 mr-0.5">*</span>}
            </span>
            {children}
        </label>
    );
}


function NumInput({ value, onChange, placeholder, testid }) {
    return (
        <input
            type="number"
            step="0.01"
            min="0"
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            className="w-full px-3 py-2 rounded-lg border border-slate-300
                       text-sm num focus:outline-none focus:ring-2
                       focus:ring-slate-900/10"
            data-testid={testid}
        />
    );
}


function LedgerEntryModal({ txnGroupId, onClose }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const { data } = await api.get(
                    `/bnpl/settlements/registered/${txnGroupId}`,
                );
                if (alive) setData(data);
            } catch (e) {
                if (alive) toast.error(errMsg(e, "فشل تحميل القيد"));
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => { alive = false; };
    }, [txnGroupId]);

    return (
        <div
            className="fixed inset-0 bg-black/40 z-50 flex items-center
                       justify-center p-4"
            onClick={onClose}
            data-testid="ledger-entry-modal"
        >
            <div
                className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl
                           max-h-[90vh] overflow-y-auto"
                onClick={(e) => e.stopPropagation()}
                dir="rtl"
            >
                <div className="p-5 border-b border-slate-200 sticky top-0
                                bg-white z-10">
                    <h2 className="text-lg font-extrabold text-slate-900">
                        تفاصيل القيد المحاسبي
                    </h2>
                    <div className="text-[11px] text-slate-500 font-mono mt-0.5">
                        {txnGroupId}
                    </div>
                </div>

                <div className="p-5">
                    {loading ? (
                        <div className="text-center text-slate-500 py-8">
                            جارٍ التحميل…
                        </div>
                    ) : !data ? (
                        <div className="text-center text-slate-400 py-8">
                            لم نتمكن من تحميل القيد.
                        </div>
                    ) : (
                        <>
                            <div className="grid grid-cols-2 sm:grid-cols-4
                                            gap-3 mb-4 bg-slate-50 rounded-xl
                                            p-3 border border-slate-200">
                                <MetricCell
                                    label="المزوّد"
                                    value={
                                        (PROVIDERS[data.meta?.provider]?.name)
                                        || data.meta?.provider}
                                />
                                <MetricCell
                                    label="رقم التسوية"
                                    value={data.meta?.settlement_reference}
                                />
                                <MetricCell
                                    label="التاريخ"
                                    value={data.meta?.settlement_date || "—"}
                                />
                                <MetricCell
                                    label="البنك"
                                    value={data.meta?.bank_account_name
                                            || "—"}
                                />
                            </div>

                            <div className="overflow-x-auto border
                                            border-slate-200 rounded-xl">
                                <table className="w-full text-xs">
                                    <thead className="bg-slate-50">
                                        <tr className="text-slate-600
                                                       font-bold">
                                            <th className="text-right px-3 py-2">
                                                #</th>
                                            <th className="text-right px-3 py-2">
                                                الحساب</th>
                                            <th className="text-right px-3 py-2">
                                                النوع الفرعي</th>
                                            <th className="text-left px-3 py-2 num">
                                                مدين</th>
                                            <th className="text-left px-3 py-2 num">
                                                دائن</th>
                                            <th className="text-right px-3 py-2">
                                                ملاحظة</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(data.entries || []).map((e, i) => (
                                            <tr
                                                key={e.id || i}
                                                className="border-t
                                                           border-slate-100"
                                                data-testid={`ledger-leg-${i}`}
                                            >
                                                <td className="px-3 py-2
                                                               text-slate-500 num">
                                                    {e.entry_no}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-slate-900
                                                               font-semibold">
                                                    {e.entity_type}.{e.entity_id}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-slate-500">
                                                    {e.sub_account || "—"}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-emerald-700
                                                               num text-left">
                                                    {e.side === "debit"
                                                      ? fmt(e.amount) : ""}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-rose-700
                                                               num text-left">
                                                    {e.side === "credit"
                                                      ? fmt(e.amount) : ""}
                                                </td>
                                                <td className="px-3 py-2
                                                               text-slate-600">
                                                    {e.notes || ""}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                    <tfoot>
                                        <tr className="border-t
                                                       border-slate-300 bg-slate-50
                                                       font-extrabold">
                                            <td className="px-3 py-2"
                                                colSpan={3}>
                                                الإجمالي
                                            </td>
                                            <td className="px-3 py-2
                                                           text-left num
                                                           text-emerald-800">
                                                {fmt(data.debit_total)}
                                            </td>
                                            <td className="px-3 py-2
                                                           text-left num
                                                           text-rose-800">
                                                {fmt(data.credit_total)}
                                            </td>
                                            <td className="px-3 py-2">
                                                {data.balanced
                                                  ? "✅ متوازن"
                                                  : "⚠ غير متوازن"}
                                            </td>
                                        </tr>
                                    </tfoot>
                                </table>
                            </div>
                        </>
                    )}
                </div>

                <div className="p-4 border-t border-slate-200 flex justify-end
                                sticky bottom-0 bg-white">
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg bg-slate-900
                                   hover:bg-slate-800 text-white text-sm
                                   font-bold"
                        data-testid="ledger-modal-close"
                    >
                        إغلاق
                    </button>
                </div>
            </div>
        </div>
    );
}


export default function BnplSettlementsRegister() {
    const [overview, setOverview] = useState({ providers: [] });
    const [recent, setRecent] = useState([]);
    const [banks, setBanks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [addFor, setAddFor] = useState(null);
    const [addPrefill, setAddPrefill] = useState(null);
    const [showEntry, setShowEntry] = useState(null);
    const [reconcile, setReconcile] = useState(null);
    const [reconcileLoading, setReconcileLoading] = useState(false);
    const [reconcilePeriod, setReconcilePeriod] = useState("last_week");

    const load = async () => {
        setLoading(true);
        try {
            const [{ data: ov }, { data: list }, { data: accs }] = await Promise.all([
                api.get("/bnpl/settlements/registration-overview"),
                api.get("/bnpl/settlements/registered", {
                    params: { limit: 25 },
                }),
                api.get("/accounts"),
            ]);
            setOverview(ov || { providers: [] });
            setRecent(list?.items || []);
            const allBanks = (Array.isArray(accs) ? accs : (accs?.items || []))
                .filter((a) => ["bank", "cash"].includes(a.account_type));
            setBanks(allBanks);
        } catch (e) {
            toast.error(errMsg(e, "فشل تحميل البيانات"));
        } finally {
            setLoading(false);
        }
    };

    const loadReconcile = async (period) => {
        setReconcileLoading(true);
        try {
            // Iter-246z — Use Asia/Riyadh "today" for week boundaries.
            const today = nowRiyadh();
            const fmt = (d) => d.toISOString().slice(0, 10);
            let from, to;
            const ms = 86400000;
            if (period === "last_week") {
                const wd = today.getUTCDay() || 7;
                const sun = new Date(today.getTime() - wd * ms);
                from = fmt(new Date(sun.getTime() - 6 * ms));
                to = fmt(sun);
            } else if (period === "this_week") {
                const wd = today.getUTCDay() || 7;
                from = fmt(new Date(today.getTime() - (wd - 1) * ms));
                to = fmt(new Date(today.getTime() + (7 - wd) * ms));
            } else if (period === "last_7d") {
                from = fmt(new Date(today.getTime() - 6 * ms));
                to = fmt(today);
            } else if (period === "this_month") {
                const f = new Date(Date.UTC(
                    today.getUTCFullYear(), today.getUTCMonth(), 1));
                from = fmt(f);
                to = fmt(today);
            } else if (period === "last_month") {
                const f = new Date(Date.UTC(
                    today.getUTCFullYear(), today.getUTCMonth() - 1, 1));
                const t = new Date(Date.UTC(
                    today.getUTCFullYear(), today.getUTCMonth(), 0));
                from = fmt(f);
                to = fmt(t);
            }
            const { data } = await api.get(
                "/bnpl/settlements/reconciliation",
                { params: { date_from: from, date_to: to } },
            );
            setReconcile(data);
        } catch (e) {
            toast.error(errMsg(e, "فشل تحميل المطابقة"));
        } finally {
            setReconcileLoading(false);
        }
    };

    useEffect(() => { load(); loadReconcile(reconcilePeriod); }, []);  // eslint-disable-line

    const openAuto = async (provider) => {
        try {
            const { data } = await api.get(
                `/bnpl/settlements/import-preview/${provider}`,
                { params: { period: "last_week" } },
            );
            setAddPrefill(data?.prefill || null);
            setAddFor(provider);
            toast.success("تم جلب القيم. راجع وعدّل ما يلزم ثم اعتمد.");
        } catch (e) {
            // Open the empty modal so the user can still enter manually.
            setAddPrefill(null);
            setAddFor(provider);
            toast.warning(
                errMsg(e, "تعذّر الجلب التلقائي — يمكنك الإدخال يدوياً"),
            );
        }
    };

    return (
        <div className="space-y-6" dir="rtl" data-testid="bnpl-register-page">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold
                                   text-slate-900">
                        تسويات تمارا وتابي وإمكان
                    </h1>
                    <p className="text-xs text-slate-500 mt-1">
                        مراجعة تسويات مزوّدي الدفع وإنشاء القيود المحاسبية في
                        الدفتر العام بعد اعتماد المحاسب.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={load}
                    className="px-3 py-2 rounded-lg border border-slate-300
                               text-xs font-bold text-slate-700 hover:bg-slate-50
                               disabled:opacity-60"
                    disabled={loading}
                    data-testid="refresh-btn"
                >
                    {loading ? "جارٍ التحديث…" : "تحديث"}
                </button>
            </div>

            {/* Overview cards */}
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                {(overview.providers || []).map((row) => (
                    <ProviderOverviewCard
                        key={row.provider}
                        row={row}
                        onOpenAdd={(p) => { setAddPrefill(null); setAddFor(p); }}
                        onOpenAuto={openAuto}
                    />
                ))}
                {loading && !overview.providers?.length && (
                    <div className="text-center text-slate-400 py-10
                                    border border-dashed border-slate-300
                                    rounded-2xl col-span-full">
                        جارٍ التحميل…
                    </div>
                )}
            </div>

            {/* Iter-223 — Reconciliation table: Expected vs Actual */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5"
                 data-testid="reconciliation-section">
                <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                    <div>
                        <h2 className="text-base font-extrabold text-slate-900">
                            مطابقة التسويات — المتوقع مقابل الفعلي
                        </h2>
                        <p className="text-[11px] text-slate-500 mt-0.5">
                            يُحتسب المتوقع من السجل بناءً على البيع/المرتجعات.
                            الفعلي من التسويات المسجَّلة في الدفتر.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <select
                            value={reconcilePeriod}
                            onChange={(e) => {
                                setReconcilePeriod(e.target.value);
                                loadReconcile(e.target.value);
                            }}
                            className="px-2 py-1.5 rounded-lg border
                                       border-slate-300 text-xs font-bold
                                       bg-white"
                            data-testid="reconcile-period"
                        >
                            <option value="last_week">الأسبوع الماضي</option>
                            <option value="this_week">هذا الأسبوع</option>
                            <option value="last_7d">آخر 7 أيام</option>
                            <option value="this_month">هذا الشهر</option>
                            <option value="last_month">الشهر الماضي</option>
                        </select>
                        <button
                            type="button"
                            onClick={() => loadReconcile(reconcilePeriod)}
                            disabled={reconcileLoading}
                            className="px-3 py-1.5 rounded-lg border
                                       border-slate-300 text-xs font-bold
                                       text-slate-700 hover:bg-slate-50
                                       disabled:opacity-60"
                            data-testid="reconcile-refresh"
                        >
                            {reconcileLoading ? "..." : "تحديث"}
                        </button>
                    </div>
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="text-slate-500 font-bold
                                           border-b border-slate-200">
                                <th className="text-right py-2 px-2">
                                    المزوّد</th>
                                <th className="text-left py-2 px-2 num">
                                    المتوقع
                                </th>
                                <th className="text-left py-2 px-2 num">
                                    الفعلي
                                </th>
                                <th className="text-left py-2 px-2 num">
                                    الفرق
                                </th>
                                <th className="text-center py-2 px-2">
                                    عدد التسويات
                                </th>
                                <th className="text-center py-2 px-2">
                                    المصدر
                                </th>
                                <th className="text-right py-2 px-2">
                                    حالة المطابقة</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(reconcile?.rows || []).map((r) => {
                                const meta = {
                                    tabby: { name: "Tabby", badge: "🟣" },
                                    tamara: { name: "Tamara", badge: "🩷" },
                                    emkan: { name: "إمكان", badge: "🟡" },
                                }[r.provider] || {};
                                return (
                                    <tr
                                        key={r.provider}
                                        className="border-b border-slate-100"
                                        data-testid={`reconcile-row-${r.provider}`}
                                    >
                                        <td className="py-2 px-2 font-bold
                                                       text-slate-900">
                                            {meta.badge} {meta.name || r.provider}
                                        </td>
                                        <td className="py-2 px-2 num text-left
                                                       text-slate-700">
                                            {fmt(r.expected)}
                                        </td>
                                        <td className="py-2 px-2 num text-left
                                                       text-emerald-700 font-bold">
                                            {fmt(r.actual)}
                                        </td>
                                        <td className={`py-2 px-2 num text-left
                                                        font-bold ${Math.abs(r.difference) < 0.5
                                            ? "text-emerald-700"
                                            : r.match_status === "yellow"
                                                ? "text-amber-700"
                                                : "text-rose-700"}`}>
                                            {r.difference >= 0 ? "+" : "−"}{fmt(Math.abs(r.difference))}
                                        </td>
                                        <td className="py-2 px-2 text-center num">
                                            {r.count}
                                        </td>
                                        <td className="py-2 px-2 text-center
                                                       text-[10px] font-mono
                                                       text-slate-500">
                                            {r.data_source === "provider_official_file"
                                                ? "📄 ملف رسمي"
                                                : "🖥️ محسوب"}
                                        </td>
                                        <td className="py-2 px-2">
                                            <StatusPill status={r.match_status} />
                                        </td>
                                    </tr>
                                );
                            })}
                            {!reconcile?.rows?.length && (
                                <tr>
                                    <td colSpan={7}
                                        className="text-center text-slate-400
                                                   py-6 text-sm">
                                        {reconcileLoading
                                            ? "جارٍ التحميل…"
                                            : "لا توجد بيانات."}
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Recent settlements list */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5">
                <h2 className="text-base font-extrabold text-slate-900 mb-3">
                    آخر التسويات المسجَّلة
                </h2>
                {recent.length === 0 ? (
                    <div className="text-center text-slate-400 py-6 text-sm">
                        لا توجد تسويات مسجَّلة بعد.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="text-slate-500 font-bold
                                               border-b border-slate-200">
                                    <th className="text-right py-2 px-2">
                                        المزوّد</th>
                                    <th className="text-right py-2 px-2">
                                        المرجع</th>
                                    <th className="text-right py-2 px-2">
                                        التاريخ</th>
                                    <th className="text-right py-2 px-2">
                                        البنك</th>
                                    <th className="text-left py-2 px-2 num">
                                        المُحوَّل</th>
                                    <th className="text-left py-2 px-2 num">
                                        العمولة</th>
                                    <th className="text-left py-2 px-2 num">
                                        VAT</th>
                                    <th className="text-left py-2 px-2 num">
                                        رسوم</th>
                                    <th className="text-left py-2 px-2 num">
                                        إجمالي مُغلَق</th>
                                    <th className="text-center py-2 px-2"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {recent.map((r) => {
                                    const meta = PROVIDERS[r.provider] || {};
                                    return (
                                        <tr
                                            key={r.txn_group_id}
                                            className="border-b border-slate-100
                                                       hover:bg-slate-50"
                                            data-testid={`recent-row-${r.settlement_reference}`}
                                        >
                                            <td className="py-2 px-2 font-bold
                                                           text-slate-900">
                                                {meta.badge} {meta.name || r.provider}
                                            </td>
                                            <td className="py-2 px-2 font-mono
                                                           text-slate-700">
                                                {r.settlement_reference}
                                            </td>
                                            <td className="py-2 px-2 text-slate-500
                                                           num">
                                                {r.settlement_date || (r.posted_at || "").slice(0, 10)}
                                            </td>
                                            <td className="py-2 px-2 text-slate-600">
                                                {r.bank_account_name || "—"}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-emerald-700">
                                                {fmt(r.transferred_amount)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-slate-600">
                                                {fmt(r.commission)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-slate-600">
                                                {fmt(r.commission_vat)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           text-slate-600">
                                                {fmt(r.settlement_fee)}
                                            </td>
                                            <td className="py-2 px-2 num text-left
                                                           font-bold text-slate-900">
                                                {fmt(r.total_closed)}
                                            </td>
                                            <td className="py-2 px-2 text-center">
                                                <button
                                                    type="button"
                                                    className="text-violet-700
                                                               text-[11px]
                                                               font-bold hover:underline"
                                                    onClick={() =>
                                                        setShowEntry(r.txn_group_id)
                                                    }
                                                    data-testid={`view-entry-${r.settlement_reference}`}
                                                >
                                                    عرض القيد
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {addFor && (
                <AddSettlementModal
                    provider={addFor}
                    banks={banks}
                    prefill={addPrefill}
                    onClose={() => { setAddFor(null); setAddPrefill(null); }}
                    onSaved={(txnGroupId) => {
                        setAddFor(null);
                        setAddPrefill(null);
                        load();
                        loadReconcile(reconcilePeriod);
                        if (txnGroupId) setShowEntry(txnGroupId);
                    }}
                />
            )}
            {showEntry && (
                <LedgerEntryModal
                    txnGroupId={showEntry}
                    onClose={() => setShowEntry(null)}
                />
            )}
        </div>
    );
}
