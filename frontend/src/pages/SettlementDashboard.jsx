/**
 * Iter-251 · Phase 2A — Settlement Engine Dashboard.
 *
 * Pure read-only dashboard backed by /api/settlement-engine/dry-run.
 * Lets the merchant inspect what the future Settlement Engine WOULD
 * generate for each provider — orders, settlements, expected pending
 * reviews, GL drift, target bank — without flipping any production
 * switch.
 *
 * Feature-flag toggles let the merchant gradually enable the real
 * engine when they're ready (defaults all off).
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (v) =>
    v == null ? "—"
        : Number(v).toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
const fmtInt = (v) =>
    v == null ? "—" : Number(v).toLocaleString("en-US");

const PROVIDER_COLORS = {
    tamara: { border: "border-amber-300",   tag: "bg-amber-100 text-amber-800"   },
    tabby:  { border: "border-sky-300",     tag: "bg-sky-100 text-sky-800"       },
    imkan:  { border: "border-violet-300",  tag: "bg-violet-100 text-violet-800" },
    salla:  { border: "border-emerald-300", tag: "bg-emerald-100 text-emerald-800" },
};

const FLAG_LABELS = {
    settlement_engine_enabled: {
        ar: "تفعيل محرّك التسويات",
        desc: "يفعّل توليد الفواتير/التسويات/التحويلات المتوقعة تلقائياً للمزودين.",
    },
    platform_settlement_to_review_enabled: {
        ar: "تحويل تسويات المنصات إلى المراجعة",
        desc: "بدل ضرب GL مباشرة، يُنشئ سجلاً في مراجعة التحويلات البنكية.",
    },
    bank_transfer_review_enabled: {
        ar: "تفعيل طبقة مراجعة التحويلات البنكية",
        desc: "السماح بإنشاء سجلات pending مرتبطة بـ /bank-transfer-review.",
    },
};

function StatTile({ label, value, color = "slate", suffix = "", testid }) {
    const palettes = {
        slate:   "bg-slate-50 text-slate-700 border-slate-200",
        emerald: "bg-emerald-50 text-emerald-800 border-emerald-200",
        amber:   "bg-amber-50 text-amber-800 border-amber-200",
        rose:    "bg-rose-50 text-rose-800 border-rose-200",
        sky:     "bg-sky-50 text-sky-800 border-sky-200",
        indigo:  "bg-indigo-50 text-indigo-800 border-indigo-200",
    };
    return (
        <div className={`rounded-lg border p-3 ${palettes[color]}`}
             data-testid={testid}>
            <div className="text-[10px] font-bold opacity-70 mb-1">{label}</div>
            <div className="text-xl font-extrabold font-mono">
                {value}{suffix}
            </div>
        </div>
    );
}

function ProviderCard({ p, onShowDetails }) {
    const c = PROVIDER_COLORS[p.provider] || PROVIDER_COLORS.salla;
    const ready = p.readiness.has_default_bank;
    return (
        <div className={`bg-white rounded-xl border-2 ${c.border} p-4 mb-3`}
             data-testid={`se-provider-${p.provider}`}>
            <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                <div className="flex items-center gap-2">
                    <span className={`text-xs font-extrabold px-3 py-1 rounded-full ${c.tag}`}>
                        {p.provider_ar}
                    </span>
                    <span className="text-[11px] text-slate-500 font-mono">
                        {p.provider}
                    </span>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => onShowDetails(p.provider)}
                        className="text-[10px] px-3 py-1 rounded-full bg-slate-900 text-white font-bold hover:bg-slate-700"
                        data-testid={`se-details-btn-${p.provider}`}
                    >
                        🔍 محاكاة الفواتير
                    </button>
                    {ready ? (
                        <span className="text-[10px] px-2 py-1 rounded bg-emerald-100 text-emerald-800 font-bold border border-emerald-300">
                            ✓ البنك: {p.default_bank.name}
                        </span>
                    ) : (
                        <span className="text-[10px] px-2 py-1 rounded bg-rose-100 text-rose-800 font-bold border border-rose-300">
                            ⚠ لا يوجد بنك افتراضي
                        </span>
                    )}
                </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
                <StatTile label="الطلبات"
                          value={fmtInt(p.orders.count)}
                          color="slate"
                          testid={`se-orders-${p.provider}`} />
                <StatTile label="التسويات الموجودة"
                          value={fmtInt(p.settlement_entries.count)}
                          color="indigo"
                          testid={`se-settlements-${p.provider}`} />
                <StatTile label="السجلات الحالية في المراجعة"
                          value={fmtInt(p.existing_reviews.total)}
                          color="amber"
                          testid={`se-reviews-${p.provider}`} />
                <StatTile label="قيود GL القديمة"
                          value={fmtInt(p.current_gl_legs)}
                          color="rose"
                          testid={`se-gl-${p.provider}`} />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
                <div className="bg-slate-50 rounded p-2">
                    <div className="font-bold mb-1">📅 الفترة</div>
                    <div className="text-slate-600 font-mono">
                        {p.orders.from || "—"} → {p.orders.to || "—"}
                    </div>
                </div>
                <div className="bg-emerald-50 rounded p-2">
                    <div className="font-bold mb-1">💰 إجمالي التسويات</div>
                    <div className="font-mono text-emerald-800">
                        {fmt(p.settlement_entries.net_total)} ر.س
                    </div>
                    <div className="text-[10px] text-slate-500 mt-1">
                        مطابقة: {p.settlement_entries.matched} / غير مطابقة: {p.settlement_entries.unmatched}
                    </div>
                </div>
                <div className="bg-amber-50 rounded p-2">
                    <div className="font-bold mb-1">🔮 المتوقع بعد التفعيل</div>
                    <div className="text-slate-700">
                        فواتير: <b className="font-mono">{fmtInt(p.expected.invoices)}</b>
                        {" "}· تسويات: <b className="font-mono">{fmtInt(p.expected.settlements)}</b>
                    </div>
                    <div className="text-slate-700 mt-0.5">
                        sجلات مراجعة جديدة: <b className="font-mono text-amber-800">
                            {fmtInt(p.expected.new_pending_reviews)}
                        </b>
                    </div>
                </div>
            </div>

            {p.existing_reviews.missing_target_bank > 0 && (
                <div className="mt-2 text-[11px] bg-rose-50 border border-rose-200 rounded p-2 text-rose-800">
                    ⚠ {p.existing_reviews.missing_target_bank} سجل عالق بحالة <b>missing_target_bank</b> — حدد البنك من الإعدادات.
                </div>
            )}
        </div>
    );
}

function FeatureFlagsPanel({ flags, onChange }) {
    return (
        <div className="bg-white rounded-xl border border-slate-200 p-4 mb-4"
             data-testid="se-feature-flags-panel">
            <h2 className="text-sm font-extrabold mb-2 flex items-center gap-2">
                🚦 Feature Flags
                <span className="text-[10px] font-normal text-slate-500">
                    (افتراضياً معطّلة — التفعيل تدريجي)
                </span>
            </h2>
            <div className="space-y-2">
                {Object.entries(FLAG_LABELS).map(([key, lbl]) => (
                    <label key={key}
                           className={`flex items-start gap-3 p-2.5 rounded border cursor-pointer transition-colors ${flags?.[key] ? "border-emerald-300 bg-emerald-50/50" : "border-slate-200 bg-white"}`}
                           data-testid={`se-flag-${key}`}>
                        <input type="checkbox"
                               className="w-4 h-4 mt-0.5 accent-emerald-600"
                               checked={!!flags?.[key]}
                               onChange={(e) => onChange(key, e.target.checked)} />
                        <div className="flex-1">
                            <div className="text-xs font-bold">{lbl.ar}</div>
                            <div className="text-[11px] text-slate-500 mt-0.5">
                                {lbl.desc}
                            </div>
                            <div className="text-[10px] font-mono text-slate-400 mt-0.5">
                                {key}
                            </div>
                        </div>
                    </label>
                ))}
            </div>
        </div>
    );
}

function CalendarPanel() {
    const [provider, setProvider] = useState("tamara");
    const [items, setItems] = useState([]);
    const [busy, setBusy] = useState(false);
    const [auditOpen, setAuditOpen] = useState(false);
    const [auditData, setAuditData] = useState(null);
    const [manual, setManual] = useState({
        invoice_date: "", period_start: "", period_end: "",
        expected_transfer_date: "",
    });

    const load = useCallback(async () => {
        try {
            const { data } = await api.get(
                "/settlement-engine/calendar",
                { params: { provider } });
            setItems(data.items || []);
        } catch (e) {
            toast.error("فشل تحميل التقويم");
        }
    }, [provider]);
    useEffect(() => { load(); }, [load]);

    const currentLayout = items[0]?.layout || (
        provider === "tamara" ? "invoice_as_start" : "invoice_as_end"
    );
    const layoutLabel = currentLayout === "invoice_as_start"
        ? "📍 تاريخ الفاتورة = أول يوم الفترة (السبت → الجمعة)"
        : "📍 تاريخ الفاتورة = آخر يوم الفترة";

    async function rebuild() {
        setBusy(true);
        try {
            const { data } = await api.post(
                "/settlement-engine/calendar/rebuild",
                { provider, dry_run: false });
            toast.success(
                `جديد: ${data.inserted} · محدّث: ${data.updated} `
                + `· مستخرج: ${data.extracted}`,
            );
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل البناء");
        } finally {
            setBusy(false);
        }
    }
    async function addManual() {
        if (!manual.invoice_date || !manual.period_start
            || !manual.period_end || !manual.expected_transfer_date) {
            toast.error("جميع التواريخ مطلوبة");
            return;
        }
        try {
            await api.post(
                "/settlement-engine/calendar/manual",
                { provider, ...manual });
            toast.success("تمت إضافة الفاتورة");
            setManual({
                invoice_date: "", period_start: "", period_end: "",
                expected_transfer_date: "",
            });
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل");
        }
    }
    async function rebuildAll() {
        if (!window.confirm(
            "سيتم إعادة بناء التقويم لجميع المزودين (تمارا/تابي/إمكان/سلة) "
            + "من settlement_entries. الإدخالات اليدوية محمية. متابعة؟",
        )) return;
        setBusy(true);
        const providers = ["tamara", "tabby", "imkan", "salla"];
        const results = [];
        const warnings = [];
        try {
            for (const p of providers) {
                try {
                    const { data } = await api.post(
                        "/settlement-engine/calendar/rebuild",
                        { provider: p, dry_run: false });
                    results.push(
                        `${p}: جديد ${data.inserted} / محدّث ${data.updated}`,
                    );
                    if (data.template_warning) {
                        warnings.push(`${p}: ${data.template_warning}`);
                    }
                } catch (e) {
                    results.push(`${p}: ❌ ${e?.response?.data?.detail || "فشل"}`);
                }
            }
            toast.success(results.join(" • "), { duration: 8000 });
            warnings.forEach(w => toast.warning(w, { duration: 14000 }));
            await load();
        } finally {
            setBusy(false);
        }
    }

    async function diagnose() {
        try {
            const { data } = await api.get(
                "/settlement-engine/calendar/diagnose",
                { params: { provider } });
            const sample = data.samples?.[0];
            const msg = [
                `📊 تشخيص ${provider}:`,
                `GL: ${data.general_ledger_settlements_found} مسجّلة`,
                `قابلة للاستخراج: ${data.from_registered_extracted}`,
                `من ملفات: ${data.from_settlement_entries_extracted}`,
                sample ? `period_from: ${sample.period_from || "—"}` : "",
                sample ? `period_to: ${sample.period_to || "—"}` : "",
            ].filter(Boolean).join(" · ");
            toast.info(msg, { duration: 12000 });
            console.log("Full diagnose:", data);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التشخيص");
        }
    }

    async function runAudit() {
        setBusy(true);
        try {
            const { data } = await api.get(
                "/settlement-engine/calendar/audit",
                { params: { provider } });
            setAuditData(data);
            setAuditOpen(true);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التدقيق");
        } finally {
            setBusy(false);
        }
    }

    async function remove(id) {
        if (!window.confirm("حذف هذه الفاتورة من التقويم؟")) return;
        try {
            await api.delete(`/settlement-engine/calendar/${id}`);
            toast.success("تم الحذف");
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الحذف");
        }
    }

    return (
        <div data-testid="se-calendar-panel">
            <div className="bg-violet-50 border border-violet-200 rounded-lg p-3 mb-4 text-xs text-violet-900">
                <div className="font-extrabold mb-1">📅 تقويم فواتير المزوّد</div>
                <div className="text-[11px] leading-relaxed">
                    تواريخ الفواتير الحقيقية المستخرجة من <code>settlement_entries</code>. يستخدمها
                    Dry-Run والـ Phase 2B لبناء الفترات بدلاً من تقسيم أسبوعي افتراضي.
                    أي تعديل هنا ينعكس مباشرة على المحاكاة والتوليد.
                </div>
                <div className="mt-2 inline-block px-2 py-1 rounded bg-white border border-violet-300 text-[10px] font-bold"
                     data-testid="se-cal-layout-badge">
                    {layoutLabel}
                </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-xl p-3 mb-4">
                <div className="flex items-center gap-2 flex-wrap mb-3">
                    <label className="text-[11px] font-bold">المزوّد
                        <select value={provider}
                                onChange={(e) => setProvider(e.target.value)}
                                className="me-2 ms-1 border rounded p-1.5"
                                data-testid="se-cal-provider">
                            <option value="tamara">تمارا</option>
                            <option value="tabby">تابي</option>
                            <option value="salla">سلة</option>
                            <option value="imkan">إمكان</option>
                        </select>
                    </label>
                    <button onClick={rebuild} disabled={busy}
                            className="text-xs font-bold px-3 py-1.5 rounded bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-40"
                            data-testid="se-cal-rebuild-btn">
                        {busy ? "..." : "🔄 إعادة بناء من settlement_entries"}
                    </button>
                    <button onClick={rebuildAll} disabled={busy}
                            className="text-xs font-bold px-3 py-1.5 rounded bg-fuchsia-600 text-white hover:bg-fuchsia-700 disabled:opacity-40"
                            data-testid="se-cal-rebuild-all-btn"
                            title="يُعيد بناء التقويم لتمارا/تابي/إمكان/سلة دفعةً واحدة">
                        {busy ? "..." : "🌐 إعادة بناء الكل"}
                    </button>
                    <button onClick={diagnose} disabled={busy}
                            className="text-xs font-bold px-3 py-1.5 rounded bg-slate-700 text-white hover:bg-slate-900 disabled:opacity-40"
                            data-testid="se-cal-diagnose-btn"
                            title="يكشف مصدر التقويم الحالي وكم تسوية مسجلة موجودة">
                        🔍 تشخيص
                    </button>
                    <button onClick={runAudit} disabled={busy}
                            className="text-xs font-bold px-3 py-1.5 rounded bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-40"
                            data-testid="se-cal-audit-btn"
                            title="تقرير Read-Only لكل فاتورة: مصدرها، هل لها مطابق في GL، وقيم metadata">
                        📋 تقرير تدقيق
                    </button>
                    <button onClick={async () => {
                        setBusy(true);
                        try {
                            const fromD = window.prompt(
                                "من تاريخ (YYYY-MM-DD)?",
                                "2026-04-27");
                            if (!fromD) return;
                            const toD = window.prompt(
                                "إلى تاريخ (YYYY-MM-DD)?",
                                "2026-06-30");
                            if (!toD) return;
                            const { data } = await api.get(
                                "/settlement-engine/calendar/raw-ledger-dump",
                                { params: { provider,
                                    "from": fromD, "to": toD } });
                            console.group(`🧬 RCA: ${provider} ${fromD} → ${toD}`);
                            console.log("Summary:", {
                                total_legs: data.total_legs,
                                total_groups: data.total_groups,
                                by_side: data.by_side,
                                by_entry_type: data.by_entry_type,
                                by_entity_type: data.by_entity_type,
                                by_status: data.by_status,
                            });
                            console.table((data.groups || []).map(g => ({
                                ref: g.settlement_reference,
                                date: g.settlement_date,
                                period: `${g.period_from || "—"} → ${g.period_to || "—"}`,
                                txn_type: g.txn_type,
                                meta_provider: g.metadata_provider,
                                legs: g.legs.length,
                                amount: g.transferred_amount,
                            })));
                            console.log("Per-calendar RCA:", data.rca_per_calendar);
                            console.log("Full groups:", data.groups);
                            console.groupEnd();
                            toast.info(
                                `🧬 RCA: ${data.total_groups} مجموعات / `
                                + `${data.total_legs} قيود · `
                                + `side: ${JSON.stringify(data.by_side)} · `
                                + `entry_type: ${JSON.stringify(data.by_entry_type)}`,
                                { duration: 18000 });
                        } catch (e) {
                            toast.error(e?.response?.data?.detail || "فشل RCA");
                        } finally { setBusy(false); }
                    }} disabled={busy}
                       className="text-xs font-bold px-3 py-1.5 rounded bg-rose-700 text-white hover:bg-rose-800 disabled:opacity-40"
                       data-testid="se-cal-rca-btn"
                       title="Read-Only: كل قيود GL لهذا المزوّد في الفترة (debit + credit، كل metadata) — للتشخيص فقط">
                        🧬 RCA دفتر اليومية
                    </button>
                    <button onClick={load}
                            className="text-xs px-3 py-1.5 rounded bg-slate-100 hover:bg-slate-200"
                            data-testid="se-cal-reload">
                        ↻ تحديث
                    </button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-5 gap-2 items-end pt-2 border-t border-slate-100">
                    <label className="text-[11px]">إضافة يدوية: تاريخ الفاتورة
                        <input type="date" value={manual.invoice_date}
                               onChange={(e) => setManual(m => ({ ...m, invoice_date: e.target.value }))}
                               className="w-full border rounded p-1.5 mt-0.5"
                               data-testid="se-cal-m-inv" />
                    </label>
                    <label className="text-[11px]">بداية الفترة
                        <input type="date" value={manual.period_start}
                               onChange={(e) => setManual(m => ({ ...m, period_start: e.target.value }))}
                               className="w-full border rounded p-1.5 mt-0.5"
                               data-testid="se-cal-m-ps" />
                    </label>
                    <label className="text-[11px]">نهاية الفترة
                        <input type="date" value={manual.period_end}
                               onChange={(e) => setManual(m => ({ ...m, period_end: e.target.value }))}
                               className="w-full border rounded p-1.5 mt-0.5"
                               data-testid="se-cal-m-pe" />
                    </label>
                    <label className="text-[11px]">تاريخ التحويل المتوقع
                        <input type="date" value={manual.expected_transfer_date}
                               onChange={(e) => setManual(m => ({ ...m, expected_transfer_date: e.target.value }))}
                               className="w-full border rounded p-1.5 mt-0.5"
                               data-testid="se-cal-m-tx" />
                    </label>
                    <button onClick={addManual}
                            className="text-xs font-bold px-3 py-1.5 rounded bg-slate-900 text-white hover:bg-slate-700"
                            data-testid="se-cal-m-add">
                        ➕ إضافة فاتورة
                    </button>
                </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden"
                 data-testid="se-cal-table">
                <div className="px-3 py-2 border-b border-slate-200 text-xs font-extrabold">
                    📋 الفواتير ({items.length})
                </div>
                <table className="min-w-full text-xs">
                    <thead className="bg-slate-50">
                        <tr className="text-right">
                            <th className="p-2">تاريخ الفاتورة</th>
                            <th className="p-2">بداية الفترة</th>
                            <th className="p-2">نهاية الفترة</th>
                            <th className="p-2">تاريخ التحويل المتوقع</th>
                            <th className="p-2 text-center">المصدر</th>
                            <th className="p-2 text-center">الطلبات</th>
                            <th className="p-2 text-center">صافي</th>
                            <th className="p-2 text-center">حذف</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.length === 0 ? (
                            <tr><td colSpan={8} className="p-6 text-center text-slate-400">
                                لا توجد فواتير في التقويم — اضغط «إعادة البناء».
                            </td></tr>
                        ) : items.map((c) => (
                            <tr key={c.id} className="border-t hover:bg-slate-50"
                                data-testid={`se-cal-row-${c.invoice_date}`}>
                                <td className="p-2 font-mono font-extrabold text-violet-700">
                                    {c.invoice_date}
                                    {c.snap_applied && (
                                        <span className="ms-2 text-[9px] bg-amber-100 text-amber-800 px-1 py-0.5 rounded"
                                              title={`الأصلي: ${(c.settlement_dates || []).join(", ")}`}>
                                            🔧 معدّل
                                        </span>
                                    )}
                                </td>
                                <td className="p-2 font-mono">{c.period_start}</td>
                                <td className="p-2 font-mono">{c.period_end}</td>
                                <td className="p-2 font-mono text-emerald-700">
                                    {c.expected_transfer_date}
                                </td>
                                <td className="p-2 text-center text-[10px]">
                                    <span className={`px-2 py-0.5 rounded font-bold ${
                                        c.source === "manual"
                                            ? "bg-amber-100 text-amber-800"
                                            : c.source === "registered_settlement"
                                                ? "bg-emerald-100 text-emerald-800"
                                                : c.layout === "templated_from_registered"
                                                    ? "bg-teal-100 text-teal-800"
                                                    : "bg-indigo-100 text-indigo-700"
                                    }`}>
                                        {c.source === "manual" ? "يدوي"
                                         : c.source === "registered_settlement" ? "📌 من تسوية مسجّلة"
                                         : c.layout === "templated_from_registered" ? "📐 من قالب مسجَّل"
                                         : "من الملفات"}
                                    </span>
                                </td>
                                <td className="p-2 text-center font-mono">
                                    {c.orders_count_hint ?? "—"}
                                </td>
                                <td className="p-2 text-center font-mono text-slate-600">
                                    {c.net_hint != null ? fmt(c.net_hint) : "—"}
                                </td>
                                <td className="p-2 text-center">
                                    <button onClick={() => remove(c.id)}
                                            className="text-[10px] text-rose-600 hover:underline"
                                            data-testid={`se-cal-del-${c.invoice_date}`}>
                                        حذف
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {auditOpen && auditData && (
                <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
                     onClick={() => setAuditOpen(false)}
                     data-testid="se-cal-audit-modal">
                    <div className="bg-white rounded-xl shadow-2xl max-w-6xl w-full max-h-[90vh] overflow-auto"
                         onClick={(e) => e.stopPropagation()}>
                        <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
                            <div className="text-sm font-extrabold">
                                📋 تقرير تدقيق Read-Only — {auditData.provider}
                            </div>
                            <button onClick={() => setAuditOpen(false)}
                                    className="text-slate-400 hover:text-slate-700 text-xl">
                                ✕
                            </button>
                        </div>
                        <div className="p-4">
                            <div className="bg-slate-50 rounded-lg p-3 mb-3 text-[11px] flex items-center gap-3 flex-wrap">
                                <span><b>صفوف التقويم:</b> {auditData.calendar_rows}</span>
                                <span><b>BNPL settlements في GL:</b> {auditData.gl_groups_found}</span>
                                {auditData.gl_side_breakdown && (
                                    <span><b>side:</b> {JSON.stringify(auditData.gl_side_breakdown)}</span>
                                )}
                                {auditData.gl_status_breakdown && (
                                    <span><b>status:</b> {JSON.stringify(auditData.gl_status_breakdown)}</span>
                                )}
                            </div>
                            <table className="min-w-full text-[11px] border border-slate-200">
                                <thead className="bg-slate-100 sticky top-0">
                                    <tr className="text-right">
                                        <th className="p-2">الفاتورة</th>
                                        <th className="p-2">period_from</th>
                                        <th className="p-2">period_to</th>
                                        <th className="p-2">المصدر</th>
                                        <th className="p-2">نوع المطابقة</th>
                                        <th className="p-2 text-center">عدد المطابقات</th>
                                        <th className="p-2">يجتاز الفلتر الصارم؟</th>
                                        <th className="p-2">GL.side</th>
                                        <th className="p-2">GL.status</th>
                                        <th className="p-2">GL.entity_id</th>
                                        <th className="p-2">GL.meta.period_from</th>
                                        <th className="p-2">GL.meta.period_to</th>
                                        <th className="p-2">GL.meta.ref</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {auditData.rows.map((r, i) => (
                                        <tr key={i} className="border-t hover:bg-slate-50">
                                            <td className="p-2 font-mono">{r.invoice_date}</td>
                                            <td className="p-2 font-mono">{r.period_from}</td>
                                            <td className="p-2 font-mono">{r.period_to}</td>
                                            <td className="p-2 text-[10px]">
                                                <span className={`px-1.5 py-0.5 rounded font-bold ${
                                                    r.source === "registered_settlement"
                                                        ? "bg-emerald-100 text-emerald-800"
                                                        : r.source === "manual"
                                                            ? "bg-amber-100 text-amber-800"
                                                            : "bg-indigo-100 text-indigo-700"
                                                }`}>
                                                    {r.source}
                                                </span>
                                            </td>
                                            <td className="p-2 text-[10px]">
                                                <span className={`px-1.5 py-0.5 rounded font-bold ${
                                                    r.match_type === "exact_period"
                                                        ? "bg-emerald-100 text-emerald-800"
                                                        : r.match_type === "overlap"
                                                            ? "bg-amber-100 text-amber-800"
                                                            : r.match_type === "by_reference"
                                                                ? "bg-violet-100 text-violet-800"
                                                                : "bg-rose-100 text-rose-700"
                                                }`}>
                                                    {r.match_type}
                                                </span>
                                            </td>
                                            <td className="p-2 text-center font-mono">{r.gl_match_count}</td>
                                            <td className="p-2 text-center">
                                                {r.gl_passes_strict_filter === true ? "✅"
                                                 : r.gl_passes_strict_filter === false ? "❌"
                                                 : "—"}
                                            </td>
                                            <td className="p-2 font-mono">{r.gl_side || "—"}</td>
                                            <td className="p-2 font-mono">{r.gl_status || "—"}</td>
                                            <td className="p-2 font-mono">{r.gl_entity_id || "—"}</td>
                                            <td className="p-2 font-mono text-emerald-700">{r.gl_metadata_period_from || "—"}</td>
                                            <td className="p-2 font-mono text-emerald-700">{r.gl_metadata_period_to || "—"}</td>
                                            <td className="p-2 font-mono text-[10px]">{r.gl_metadata_settlement_ref || "—"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            <div className="mt-3 text-[11px] text-slate-500 leading-relaxed">
                                {auditData.note}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function GeneratedPanel({
    flags, stats, invoices, provider, setProvider,
    dateFrom, setDateFrom, dateTo, setDateTo,
    busy, onGenerate, onCancel, onReload,
}) {
    const engineOn = !!flags?.settlement_engine_enabled;
    const STATUS_COLORS = {
        draft: "bg-slate-100 text-slate-700",
        generated: "bg-indigo-100 text-indigo-700",
        waiting_transfer: "bg-amber-100 text-amber-800",
        pending_review: "bg-violet-100 text-violet-800",
        confirmed: "bg-emerald-100 text-emerald-800",
        confirmed_with_difference: "bg-yellow-100 text-yellow-800",
        cancelled: "bg-rose-100 text-rose-700",
    };
    return (
        <div data-testid="se-generated-panel">
            {/* Flag warning */}
            <div className={`rounded-lg p-3 mb-3 border ${
                engineOn
                    ? "bg-emerald-50 border-emerald-200 text-emerald-900"
                    : "bg-rose-50 border-rose-200 text-rose-900"
            }`}>
                <div className="text-xs font-extrabold">
                    {engineOn
                        ? "✅ محرّك التسويات مفعَّل — التوليد يحفظ في القاعدة."
                        : "🚫 محرّك التسويات معطّل — يمكن تنفيذ Dry-Run فقط."}
                </div>
                <div className="text-[11px] mt-0.5 opacity-80">
                    Feature Flag: <code>settlement_engine_enabled</code>
                    {!engineOn && " — فعّله من تبويب Dry-Run > Feature Flags."}
                </div>
            </div>

            {/* Stats */}
            {stats && (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
                    <StatTile label="فترات تسوية"
                              value={fmtInt(stats.summary?.periods?.total)}
                              color="indigo"
                              testid="se-gen-stats-periods" />
                    <StatTile label="فواتير مولّدة"
                              value={fmtInt(stats.summary?.invoices?.total)}
                              color="emerald"
                              testid="se-gen-stats-invoices" />
                    <StatTile label="تحويلات متوقعة"
                              value={fmtInt(stats.summary?.expected_transfers?.total)}
                              color="amber"
                              testid="se-gen-stats-xfers" />
                </div>
            )}

            {/* Generation form */}
            <div className="bg-white border border-slate-200 rounded-xl p-3 mb-4"
                 data-testid="se-gen-form">
                <div className="text-xs font-extrabold mb-2">
                    🛠️ توليد فواتير وتسويات
                </div>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-2 items-end">
                    <label className="text-[11px]">
                        المزوّد
                        <select value={provider}
                                onChange={(e) => setProvider(e.target.value)}
                                className="w-full border rounded p-1.5 mt-0.5"
                                data-testid="se-gen-provider">
                            <option value="salla">سلة</option>
                            <option value="tamara">تمارا</option>
                            <option value="tabby">تابي</option>
                            <option value="imkan">إمكان</option>
                        </select>
                    </label>
                    <label className="text-[11px]">
                        من تاريخ
                        <input type="date" value={dateFrom}
                               onChange={(e) => setDateFrom(e.target.value)}
                               className="w-full border rounded p-1.5 mt-0.5"
                               data-testid="se-gen-from" />
                    </label>
                    <label className="text-[11px]">
                        إلى تاريخ
                        <input type="date" value={dateTo}
                               onChange={(e) => setDateTo(e.target.value)}
                               className="w-full border rounded p-1.5 mt-0.5"
                               data-testid="se-gen-to" />
                    </label>
                    <div className="flex gap-2">
                        <button onClick={() => onGenerate(true)}
                                disabled={busy}
                                className="flex-1 text-xs font-bold px-3 py-1.5 rounded bg-slate-700 text-white hover:bg-slate-900 disabled:opacity-40"
                                data-testid="se-gen-dry-btn">
                            {busy ? "..." : "Dry-Run"}
                        </button>
                        <button onClick={() => onGenerate(false)}
                                disabled={busy || !engineOn}
                                className="flex-1 text-xs font-bold px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
                                title={engineOn ? "" : "فعّل Feature Flag أولاً"}
                                data-testid="se-gen-real-btn">
                            {busy ? "..." : "توليد فعلي"}
                        </button>
                    </div>
                </div>
                <div className="text-[10px] text-slate-500 mt-2">
                    📐 تستخدم نفس قواعد <code>_merchant_fee_rates</code> المركزية لـ BNPL،
                    وملفات <code>settlement_entries</code> لسلة. لا يوجد أي قيمة hard-coded.
                </div>
            </div>

            {/* Invoices table */}
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden"
                 data-testid="se-gen-invoices-table">
                <div className="px-3 py-2 border-b border-slate-200 flex items-center justify-between">
                    <div className="text-xs font-extrabold">
                        📦 الفواتير المولّدة ({invoices.length})
                    </div>
                    <button onClick={onReload}
                            className="text-[10px] px-2 py-1 rounded bg-slate-100 hover:bg-slate-200"
                            data-testid="se-gen-reload-btn">
                        🔄 تحديث
                    </button>
                </div>
                <table className="min-w-full text-xs">
                    <thead className="bg-slate-50">
                        <tr className="text-right">
                            <th className="p-2">المزوّد</th>
                            <th className="p-2">رقم الفاتورة</th>
                            <th className="p-2">الفترة</th>
                            <th className="p-2 text-center">طلبات</th>
                            <th className="p-2 text-center">المتوقع تحويله</th>
                            <th className="p-2 text-center">البنك</th>
                            <th className="p-2 text-center">الحالة</th>
                            <th className="p-2 text-center">إجراءات</th>
                        </tr>
                    </thead>
                    <tbody>
                        {invoices.length === 0 ? (
                            <tr>
                                <td colSpan={8} className="p-6 text-center text-slate-400">
                                    لا توجد فواتير مولّدة بعد — استخدم النموذج أعلاه.
                                </td>
                            </tr>
                        ) : invoices.map((inv) => (
                            <tr key={inv.id} className="border-t hover:bg-slate-50"
                                data-testid={`se-gen-inv-${inv.id}`}>
                                <td className="p-2 font-mono">{inv.provider_name}</td>
                                <td className="p-2 font-mono text-[10px]">{inv.id.slice(0, 8)}…</td>
                                <td className="p-2 font-mono text-[11px] text-slate-600">
                                    {inv.period_from} → {inv.period_to}
                                </td>
                                <td className="p-2 text-center font-mono">
                                    {fmtInt(inv.source_orders_count)}
                                </td>
                                <td className="p-2 text-center font-mono text-indigo-700 font-extrabold">
                                    {fmt(inv.expected_transfer_amount)}
                                </td>
                                <td className="p-2 text-center text-[10px] text-slate-500">
                                    —
                                </td>
                                <td className="p-2 text-center">
                                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${STATUS_COLORS[inv.status] || "bg-slate-100"}`}>
                                        {inv.status}
                                    </span>
                                </td>
                                <td className="p-2 text-center">
                                    {!["confirmed", "confirmed_with_difference", "cancelled"].includes(inv.status) && (
                                        <button onClick={() => onCancel(inv.id)}
                                                className="text-[10px] text-rose-600 hover:underline"
                                                data-testid={`se-gen-cancel-${inv.id}`}>
                                            إلغاء
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default function SettlementDashboard() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [detailsProvider, setDetailsProvider] = useState(null);
    const [detailsData, setDetailsData] = useState(null);
    const [detailsLoading, setDetailsLoading] = useState(false);
    // Iter-251 · Phase 2B — generated documents
    const [activeTab, setActiveTab] = useState("dry_run");
    const [genStats, setGenStats] = useState(null);
    const [genInvoices, setGenInvoices] = useState([]);
    const [genProvider, setGenProvider] = useState("salla");
    const [genFrom, setGenFrom] = useState("");
    const [genTo, setGenTo] = useState("");
    const [genBusy, setGenBusy] = useState(false);

    async function loadGenStats() {
        try {
            const { data: s } = await api.get("/settlement-engine/stats");
            setGenStats(s);
        } catch (e) { /* silent */ }
    }
    async function loadGenInvoices(providerFilter = null) {
        try {
            const params = {};
            if (providerFilter) params.provider = providerFilter;
            const { data: r } = await api.get(
                "/settlement-engine/invoices", { params });
            setGenInvoices(r.items || []);
        } catch (e) {
            toast.error("فشل تحميل الفواتير المولّدة");
        }
    }
    async function runGenerate(dryRun = false) {
        if (!genProvider) return;
        setGenBusy(true);
        try {
            const { data: r } = await api.post(
                "/settlement-engine/generate",
                { provider: genProvider,
                  date_from: genFrom || null,
                  date_to:   genTo || null,
                  dry_run:   dryRun });
            const c = r.counts || {};
            if (dryRun) {
                toast.success(
                    `محاكاة: ${c.invoices_new || 0} فاتورة جديدة `
                    + `+ ${c.expected_transfers_new || 0} تحويل متوقع`,
                );
            } else if (r.rule_source_missing) {
                toast.warning(
                    "هذا المزوّد لا يحوي مصدر قواعد مركزي بعد. "
                    + (r.note || ""));
            } else {
                toast.success(
                    `تم التوليد: ${c.invoices_new || 0} جديدة، `
                    + `${c.invoices_reused || 0} موجودة مسبقاً.`,
                );
                await Promise.all([loadGenStats(), loadGenInvoices()]);
            }
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التوليد");
        } finally {
            setGenBusy(false);
        }
    }
    async function cancelInvoice(invId) {
        const reason = window.prompt("سبب إلغاء الفاتورة؟");
        if (!reason) return;
        try {
            await api.post(
                `/settlement-engine/invoices/${invId}/cancel`,
                { reason });
            toast.success("تم الإلغاء");
            await Promise.all([loadGenStats(), loadGenInvoices()]);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الإلغاء");
        }
    }
    useEffect(() => {
        if (activeTab === "generated") {
            loadGenStats();
            loadGenInvoices();
        }
    }, [activeTab]);

    async function showDetails(prov) {
        setDetailsProvider(prov);
        setDetailsLoading(true);
        setDetailsData(null);
        try {
            const { data: d } = await api.get(
                "/settlement-engine/dry-run-details",
                { params: { provider: prov } });
            setDetailsData(d.providers[prov]);
        } catch (e) {
            toast.error("فشل تحميل التفاصيل");
        } finally {
            setDetailsLoading(false);
        }
    }

    const reload = useCallback(async () => {
        setLoading(true);
        try {
            const { data: d } = await api.get("/settlement-engine/dry-run");
            setData(d);
        } catch (e) {
            toast.error("فشل تحميل تقرير Dry-Run");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { reload(); }, [reload]);

    async function toggleFlag(key, value) {
        try {
            const { data: flags } = await api.patch(
                "/settlement-engine/feature-flags", { [key]: value });
            setData((s) => s ? { ...s, feature_flags: flags } : s);
            toast.success("تم حفظ الإعداد");
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل التعديل");
        }
    }

    return (
        <div className="p-4" dir="rtl" data-testid="settlement-dashboard-page">
            <div className="mb-4">
                <h1 className="text-xl font-extrabold text-slate-900 flex items-center gap-2">
                    🔬 لوحة محرّك التسويات
                </h1>
                <p className="text-xs text-slate-500 max-w-3xl mt-1">
                    تحليل قراءة فقط لما سيتم توليده عند تفعيل محرّك التسويات للمزودين الأربعة
                    (سلة، تمارا، تابي، إمكان). <b>لا كتابة في القاعدة</b>، لا تعديل GL، لا مساس بالـ webhooks الحالية.
                </p>
            </div>

            {/* ─── Tabs ─── */}
            <div className="flex items-center gap-2 mb-4 border-b border-slate-200">
                <button
                    onClick={() => setActiveTab("dry_run")}
                    className={`px-4 py-2 text-xs font-extrabold border-b-2 -mb-px ${
                        activeTab === "dry_run"
                            ? "border-indigo-500 text-indigo-700"
                            : "border-transparent text-slate-500 hover:text-slate-700"
                    }`}
                    data-testid="se-tab-dryrun"
                >
                    🔬 Dry-Run
                </button>
                <button
                    onClick={() => setActiveTab("calendar")}
                    className={`px-4 py-2 text-xs font-extrabold border-b-2 -mb-px ${
                        activeTab === "calendar"
                            ? "border-violet-500 text-violet-700"
                            : "border-transparent text-slate-500 hover:text-slate-700"
                    }`}
                    data-testid="se-tab-calendar"
                >
                    📅 تقويم الفواتير
                </button>
                <button
                    onClick={() => setActiveTab("generated")}
                    className={`px-4 py-2 text-xs font-extrabold border-b-2 -mb-px ${
                        activeTab === "generated"
                            ? "border-emerald-500 text-emerald-700"
                            : "border-transparent text-slate-500 hover:text-slate-700"
                    }`}
                    data-testid="se-tab-generated"
                >
                    📦 الفواتير المولّدة (Phase 2B)
                </button>
            </div>

            {activeTab === "calendar" && (
                <CalendarPanel />
            )}

            {activeTab === "generated" && (
                <GeneratedPanel
                    flags={data?.feature_flags}
                    stats={genStats}
                    invoices={genInvoices}
                    provider={genProvider} setProvider={setGenProvider}
                    dateFrom={genFrom} setDateFrom={setGenFrom}
                    dateTo={genTo} setDateTo={setGenTo}
                    busy={genBusy}
                    onGenerate={runGenerate}
                    onCancel={cancelInvoice}
                    onReload={() => { loadGenStats(); loadGenInvoices(); }}
                />
            )}

            {activeTab === "dry_run" && (loading ? (
                <div className="text-center text-slate-500 py-16">جارٍ التحميل...</div>
            ) : !data ? (
                <div className="text-center text-rose-500 py-16">تعذّر تحميل التقرير</div>
            ) : (
                <>
                    <FeatureFlagsPanel
                        flags={data.feature_flags}
                        onChange={toggleFlag}
                    />

                    {/* Summary tiles */}
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                        <StatTile label="عدد المزودين"
                                  value={fmtInt(data.summary.providers)}
                                  color="slate" />
                        <StatTile label="إجمالي الطلبات"
                                  value={fmtInt(data.summary.total_orders)}
                                  color="indigo" />
                        <StatTile label="إجمالي صفوف التسويات"
                                  value={fmtInt(data.summary.total_settlement_rows)}
                                  color="emerald" />
                        <StatTile label="سجلات مراجعة جديدة متوقعة"
                                  value={fmtInt(data.summary.expected_new_pending_reviews)}
                                  color="amber" />
                        <StatTile label="قيود GL قديمة (legacy)"
                                  value={fmtInt(data.summary.existing_gl_legs_legacy)}
                                  color="rose" />
                    </div>

                    <div className="bg-slate-50 rounded-xl p-3 mb-3 text-[11px] text-slate-600 flex items-center gap-2 flex-wrap"
                         data-testid="se-review-totals">
                        <b>السجلات الحالية في المراجعة:</b>
                        {Object.entries(data.summary.review_totals || {}).map(([k, v]) => (
                            <span key={k} className="px-2 py-0.5 rounded bg-white border border-slate-200">
                                {k}: <b className="font-mono">{v}</b>
                            </span>
                        ))}
                        {Object.keys(data.summary.review_totals || {}).length === 0 && (
                            <span className="text-slate-400">— لا توجد سجلات بعد —</span>
                        )}
                    </div>

                    {/* Per-provider cards */}
                    <div data-testid="se-per-provider">
                        {data.per_provider.map((p) =>
                            <ProviderCard key={p.provider} p={p}
                                          onShowDetails={showDetails} />)}
                    </div>

                    {/* Notes / disclaimers */}
                    <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 mt-4 text-[11px] text-amber-900">
                        <div className="font-extrabold mb-1">📝 ملاحظات حرجة:</div>
                        <ul className="list-disc ms-5 space-y-0.5">
                            {(data.notes || []).map((n, i) => <li key={i}>{n}</li>)}
                        </ul>
                    </div>
                </>
            ))}

            {/* Dry-Run Details Modal */}
            {detailsProvider && (
                <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                     onClick={() => setDetailsProvider(null)}
                     data-testid="se-details-modal">
                    <div className="bg-white rounded-2xl shadow-2xl max-w-5xl w-full max-h-[90vh] overflow-y-auto"
                         onClick={(e) => e.stopPropagation()}>
                        <div className="sticky top-0 bg-white border-b border-slate-200 p-4 flex items-center justify-between">
                            <h3 className="text-base font-extrabold">
                                🔍 محاكاة الفواتير — {detailsData?.provider_ar || detailsProvider}
                            </h3>
                            <button onClick={() => setDetailsProvider(null)}
                                    className="text-slate-400 hover:text-slate-700 text-xl"
                                    data-testid="se-details-close">✕</button>
                        </div>
                        <div className="p-4">
                            {detailsLoading ? (
                                <div className="text-center text-slate-500 py-12">جارٍ المحاكاة...</div>
                            ) : !detailsData ? (
                                <div className="text-center text-rose-500 py-12">تعذّر التحميل</div>
                            ) : (
                                <>
                                    {detailsData.cycle && detailsData.cycle.uses_calendar === false
                                     && ["tamara","tabby","imkan"].includes(detailsData.provider) && (
                                        <div className="bg-rose-50 border-2 border-rose-300 rounded-lg p-3 mb-3"
                                             data-testid="se-no-calendar-warning">
                                            <div className="text-rose-900 font-extrabold text-xs mb-1">
                                                ⚠️ لا يوجد تقويم لهذا المزوّد — الأرقام تقريبية!
                                            </div>
                                            <div className="text-rose-800 text-[11px] leading-relaxed">
                                                هذه المحاكاة تستخدم <code>unified_orders</code> × عمولة مسطّحة (وضع fallback).
                                                للحصول على نفس أرقام صفحة BNPL الفعلية، اذهب إلى تبويب
                                                <b> «📅 تقويم الفواتير»</b> واضغط
                                                <b> «🌐 إعادة بناء الكل»</b>، ثم أعِد فتح هذه المحاكاة.
                                            </div>
                                        </div>
                                    )}
                                    <div className="bg-slate-50 rounded-lg p-3 mb-3 text-[11px] text-slate-600 flex items-center gap-2 flex-wrap">
                                        {detailsData.formula_source && (
                                            <span className={`px-2 py-0.5 rounded font-extrabold ${
                                                detailsData.formula_source === "BNPL Settlement Formula (Real)"
                                                    ? "bg-emerald-100 text-emerald-800 border border-emerald-300"
                                                    : detailsData.formula_source === "Actual Settlement Formula"
                                                        ? "bg-emerald-100 text-emerald-800 border border-emerald-300"
                                                        : detailsData.formula_source === "BNPL Settlement Formula"
                                                            ? "bg-indigo-100 text-indigo-800 border border-indigo-300"
                                                            : "bg-amber-100 text-amber-800 border border-amber-300"
                                            }`} data-testid="se-formula-source">
                                                📐 {detailsData.formula_source}
                                            </span>
                                        )}
                                        <b>المصدر:</b> {detailsData.source}
                                        {detailsData.rules && (
                                            <>
                                                {" • "}
                                                <b>العمولة:</b> {(detailsData.rules.commission_rate*100).toFixed(2)}%
                                                {" • "}
                                                <b>VAT:</b> {(detailsData.rules.vat_rate_on_commission*100).toFixed(2)}%
                                                {" • "}
                                                <span className="text-[10px] text-slate-500">
                                                    (fee_source: <code>{detailsData.rules.fee_source}</code>)
                                                </span>
                                            </>
                                        )}
                                    </div>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
                                        <StatTile label="عدد الفواتير المتوقعة"
                                                  value={fmtInt(detailsData.totals.invoices_count)} color="indigo" />
                                        <StatTile label="إجمالي الطلبات"
                                                  value={fmtInt(detailsData.totals.orders_count)} color="slate" />
                                        <StatTile label="إجمالي المبيعات"
                                                  value={fmt(detailsData.totals.gross_sales)} suffix=" ر.س" color="emerald" />
                                        <StatTile label="صافي التحويل المتوقع"
                                                  value={fmt(detailsData.totals.expected_transfer)} suffix=" ر.س" color="amber" />
                                    </div>
                                    <table className="min-w-full text-xs border border-slate-200 rounded-lg overflow-hidden">
                                        <thead className="bg-slate-100">
                                            <tr className="text-right">
                                                <th className="p-2">رقم</th>
                                                <th className="p-2">تاريخ الفاتورة</th>
                                                <th className="p-2">الفترة</th>
                                                <th className="p-2">تاريخ التحويل المتوقع</th>
                                                <th className="p-2 text-center">الطلبات</th>
                                                <th className="p-2 text-center">إجمالي</th>
                                                <th className="p-2 text-center">مرتجعات</th>
                                                <th className="p-2 text-center">العمولة</th>
                                                <th className="p-2 text-center">VAT عمولة</th>
                                                <th className="p-2 text-center">رسوم التسوية</th>
                                                <th className="p-2 text-center">VAT رسوم</th>
                                                <th className="p-2 text-center">صافي التحويل</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {detailsData.invoices.map((inv, i) => (
                                                <tr key={i} className="border-t hover:bg-slate-50">
                                                    <td className="p-2 font-mono font-bold">{inv.dry_invoice_id}</td>
                                                    <td className="p-2 font-mono font-extrabold text-violet-700">
                                                        {inv.invoice_date || "—"}
                                                    </td>
                                                    <td className="p-2 font-mono text-slate-600">
                                                        {inv.period_from} → {inv.period_to}
                                                    </td>
                                                    <td className="p-2 font-mono text-emerald-700">
                                                        {inv.expected_transfer_date || "—"}
                                                    </td>
                                                    <td className="p-2 text-center font-mono">{fmtInt(inv.orders_count)}</td>
                                                    <td className="p-2 text-center font-mono text-emerald-700">{fmt(inv.gross_sales)}</td>
                                                    <td className="p-2 text-center font-mono text-rose-700">{fmt(inv.refunds)}</td>
                                                    <td className="p-2 text-center font-mono text-amber-700">{fmt(inv.estimated_commission)}</td>
                                                    <td className="p-2 text-center font-mono text-violet-700">{fmt(inv.estimated_vat)}</td>
                                                    <td className="p-2 text-center font-mono text-slate-600">{fmt(inv.settlement_fee || 0)}</td>
                                                    <td className="p-2 text-center font-mono text-slate-500">{fmt(inv.settlement_fee_vat || 0)}</td>
                                                    <td className="p-2 text-center font-mono text-indigo-800 font-extrabold">{fmt(inv.expected_transfer)}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                    {detailsData.invoices.length === 0 && (
                                        <div className="text-center text-slate-500 py-6">
                                            لا توجد فواتير محاكاة لهذا المزود.
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
