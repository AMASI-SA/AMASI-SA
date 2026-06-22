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

function ProviderCard({ p }) {
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
                    {ready ? (
                        <span className="text-[10px] px-2 py-1 rounded bg-emerald-100 text-emerald-800 font-bold border border-emerald-300">
                            ✓ البنك مُحدَّد: {p.default_bank.name}
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

export default function SettlementDashboard() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

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
                    🔬 لوحة محرّك التسويات — Dry Run
                </h1>
                <p className="text-xs text-slate-500 max-w-3xl mt-1">
                    تحليل قراءة فقط لما سيتم توليده عند تفعيل محرّك التسويات للمزودين الأربعة
                    (سلة، تمارا، تابي، إمكان). <b>لا كتابة في القاعدة</b>، لا تعديل GL، لا مساس بالـ webhooks الحالية.
                </p>
            </div>

            {loading ? (
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
                            <ProviderCard key={p.provider} p={p} />)}
                    </div>

                    {/* Notes / disclaimers */}
                    <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 mt-4 text-[11px] text-amber-900">
                        <div className="font-extrabold mb-1">📝 ملاحظات حرجة:</div>
                        <ul className="list-disc ms-5 space-y-0.5">
                            {(data.notes || []).map((n, i) => <li key={i}>{n}</li>)}
                        </ul>
                    </div>
                </>
            )}
        </div>
    );
}
