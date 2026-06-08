/**
 * Custom App Integration — Settings + Monitoring (Iter-105)
 *
 * Two tabs in one page:
 *   1. الإعدادات          → API key, webhook URLs, copy buttons, regenerate, enable/disable.
 *   2. المراقبة والسجل    → counters, last order, errors, recent events log.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    Plug, Copy, ArrowsClockwise, CheckCircle, XCircle, Eye, EyeSlash,
    Lightning, ShoppingBag, Package, Users, Warning, Clock, FileText,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";


const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

const fmtDateTime = (iso) => {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString("ar-SA", {
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit",
        });
    } catch { return iso; }
};


function CopyField({ value, label, masked, testid }) {
    const [revealed, setRevealed] = useState(!masked);
    const display = revealed ? value : "•".repeat(Math.min(40, (value || "").length));

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(value);
            toast.success("تم النسخ");
        } catch {
            toast.error("تعذّر النسخ");
        }
    };

    return (
        <div className="space-y-1">
            <label className="block text-xs font-bold text-slate-700">{label}</label>
            <div className="flex items-stretch gap-1">
                <input
                    value={display}
                    readOnly
                    className="flex-1 px-3 py-2 text-xs font-mono border border-slate-300 rounded-lg bg-slate-50 text-slate-900"
                    data-testid={testid}
                    dir="ltr"
                />
                {masked && (
                    <button onClick={() => setRevealed((r) => !r)} className="px-2 py-2 rounded-lg bg-white border border-slate-300 hover:bg-slate-50" title={revealed ? "إخفاء" : "كشف"}>
                        {revealed ? <EyeSlash size={16} /> : <Eye size={16} />}
                    </button>
                )}
                <button onClick={copy} className="px-2 py-2 rounded-lg bg-white border border-slate-300 hover:bg-slate-50" title="نسخ">
                    <Copy size={16} />
                </button>
            </div>
        </div>
    );
}


// ── Settings tab ────────────────────────────────────────────────────
function SettingsTab({ settings, onChanged }) {
    const [busy, setBusy] = useState(false);

    if (!settings) {
        return <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div>;
    }

    const rotate = async () => {
        if (!window.confirm("هل تريد تدوير المفتاح؟ المفتاح القديم سيتوقف فوراً ولن يعمل تطبيقك حتى تستبدل المفتاح في إعداداته.")) return;
        setBusy(true);
        try {
            const { data } = await api.post("/integrations/custom-app/settings/api-key/regenerate");
            toast.success("تم توليد مفتاح جديد. حدِّث تطبيقك بأسرع وقت.");
            onChanged({ ...settings, api_key: data.api_key, rotated_at: data.rotated_at });
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التدوير");
        } finally { setBusy(false); }
    };

    const toggle = async () => {
        const next = !settings.enabled;
        if (!next && !window.confirm("إيقاف الربط سيرفض كل الطلبات الواردة. متأكد؟")) return;
        try {
            await api.post("/integrations/custom-app/settings/toggle", { enabled: next });
            toast.success(next ? "تم تفعيل الربط" : "تم إيقاف الربط");
            onChanged({ ...settings, enabled: next });
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }
    };

    const ep = settings.endpoints || {};

    return (
        <div className="space-y-6">
            <div className={`rounded-xl border p-4 flex items-center justify-between ${settings.enabled ? "bg-emerald-50 border-emerald-200" : "bg-rose-50 border-rose-200"}`} data-testid="capi-status-banner">
                <div className="flex items-center gap-2 text-sm">
                    {settings.enabled
                        ? <CheckCircle size={20} weight="fill" className="text-emerald-600" />
                        : <XCircle size={20} weight="fill" className="text-rose-600" />}
                    <div>
                        <div className={`font-bold ${settings.enabled ? "text-emerald-900" : "text-rose-900"}`}>
                            {settings.enabled ? "الربط مفعَّل ويستقبل الطلبات" : "الربط متوقَّف"}
                        </div>
                        <div className={`text-xs ${settings.enabled ? "text-emerald-700" : "text-rose-700"}`}>
                            {settings.created_at ? `أُنشئ في ${fmtDateTime(settings.created_at)}` : ""}
                            {settings.rotated_at ? ` · آخر تدوير ${fmtDateTime(settings.rotated_at)}` : ""}
                        </div>
                    </div>
                </div>
                <button onClick={toggle} className={`px-3 py-1.5 rounded-lg text-xs font-bold ${settings.enabled ? "bg-rose-700 text-white hover:bg-rose-800" : "bg-emerald-700 text-white hover:bg-emerald-800"}`} data-testid="capi-toggle-btn">
                    {settings.enabled ? "إيقاف" : "تفعيل"}
                </button>
            </div>

            <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
                <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                    <h3 className="font-bold text-base text-slate-900">🔑 مفتاح API</h3>
                    <button onClick={rotate} disabled={busy} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-violet-700 text-white text-xs font-bold hover:bg-violet-800 disabled:opacity-50" data-testid="capi-rotate-btn">
                        <ArrowsClockwise size={12} /> توليد مفتاح جديد
                    </button>
                </div>
                <CopyField value={settings.api_key} label="API Key" masked testid="capi-api-key" />
                <div className="text-xs text-slate-600 bg-amber-50 border border-amber-200 rounded p-3 leading-6">
                    ⚠️ <b>أرسل هذا المفتاح في كل طلب</b> عبر الهيدر التالي:
                    <pre className="font-mono mt-1 bg-white border border-slate-200 rounded p-2 text-[11px] text-slate-800" dir="ltr">X-API-Key: {settings.api_key?.slice(0, 12)}…</pre>
                    تدوير المفتاح يُلغي القديم فوراً.
                </div>
            </section>

            <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
                <h3 className="font-bold text-base text-slate-900 border-b border-slate-100 pb-3">🌐 روابط الـ Endpoints</h3>
                <CopyField value={ep.orders}    label="POST · إرسال الطلبات" testid="capi-ep-orders" />
                <CopyField value={ep.products}  label="POST · إرسال المنتجات" testid="capi-ep-products" />
                <CopyField value={ep.customers} label="POST · إرسال العملاء" testid="capi-ep-customers" />
                <CopyField value={ep.test}      label="POST · اختبار الاتصال" testid="capi-ep-test" />
            </section>

            <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
                <h3 className="font-bold text-base text-slate-900 border-b border-slate-100 pb-3">📦 مثال طلب JSON</h3>
                <pre className="font-mono text-[11px] text-slate-700 bg-slate-50 border border-slate-200 rounded p-3 overflow-x-auto leading-5" dir="ltr">{`POST ${ep.orders}
Headers:
  X-API-Key: <your-api-key>
  Content-Type: application/json

Body:
{
  "order_number": "ORD-1001",
  "created_at": "2026-06-10T12:00:00Z",
  "order_status": "تم التوصيل",
  "payment_status": "paid",
  "payment_method": "cash_on_delivery",
  "currency": "SAR",
  "subtotal": 150, "discount": 10, "shipping_cost": 20,
  "tax": 18, "total_amount": 178,
  "customer_name": "أحمد",
  "mobile": "0555555555",
  "shipping_company": "أرامكس",
  "tracking_number": "TRK-AAA",
  "utm_source": "snapchat",
  "items": [
    {"product_name": "منتج A", "sku": "SKU-A",
     "quantity": 2, "unit_price": 50, "cost_price": 30},
    {"product_name": "منتج B", "sku": "SKU-B",
     "quantity": 1, "unit_price": 50}
  ]
}`}</pre>
            </section>
        </div>
    );
}


// ── Monitoring tab ──────────────────────────────────────────────────
const STATUS_BADGE = {
    success: { tone: "bg-emerald-50 text-emerald-800 border-emerald-200", icon: CheckCircle, label: "نجح" },
    error:   { tone: "bg-rose-50 text-rose-800 border-rose-200",          icon: XCircle,   label: "فشل" },
};

const EVENT_LABEL = {
    orders:    "طلبات",
    products:  "منتجات",
    customers: "عملاء",
    test:      "اختبار اتصال",
};

function MonitoringTab() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/integrations/custom-app/status");
            setData(data);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setLoading(false); }
    };
    useEffect(() => {
        load();
        const t = setInterval(load, 30000);   // refresh every 30s
        return () => clearInterval(t);
    }, []);

    if (loading || !data) {
        return <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div>;
    }

    const conn = data.connection_status;
    const connTone = conn === "connected" ? "emerald" : conn === "error" ? "rose" : "amber";

    return (
        <div className="space-y-5">
            <div className={`rounded-xl border p-4 flex items-center justify-between bg-${connTone}-50 border-${connTone}-200`}>
                <div className="flex items-center gap-2">
                    {conn === "connected" ? (
                        <Lightning size={22} weight="fill" className="text-emerald-600" />
                    ) : conn === "error" ? (
                        <Warning size={22} weight="fill" className="text-rose-600" />
                    ) : (
                        <Clock size={22} weight="fill" className="text-amber-600" />
                    )}
                    <div>
                        <div className={`font-bold text-${connTone}-900`}>
                            {conn === "connected" ? "متصل ويستقبل" : conn === "error" ? "أحدث محاولة فشلت" : "لا توجد بيانات بعد"}
                        </div>
                        {data.last_sync_at && (
                            <div className={`text-xs text-${connTone}-700`}>آخر مزامنة ناجحة: {fmtDateTime(data.last_sync_at)}</div>
                        )}
                    </div>
                </div>
                <button onClick={load} className="px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-slate-700 text-xs font-bold hover:bg-slate-50">
                    <ArrowsClockwise size={12} className="inline ml-1" /> تحديث
                </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                <KPI title="الطلبات المستلمة"  value={data.orders_count}    Icon={ShoppingBag} testid="capi-kpi-orders" />
                <KPI title="المنتجات"          value={data.products_count}  Icon={Package}     testid="capi-kpi-products" />
                <KPI title="العملاء"           value={data.customers_count} Icon={Users}       testid="capi-kpi-customers" />
                <KPI title="الأخطاء" value={data.errors_count} tone={data.errors_count > 0 ? "rose" : "emerald"} Icon={Warning} testid="capi-kpi-errors" />
            </div>

            {data.last_order && (
                <section className="bg-white border border-slate-200 rounded-xl p-4">
                    <div className="text-xs font-bold text-slate-600 mb-2">آخر طلب مستلم</div>
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
                        <div><b className="text-slate-900">{data.last_order.order_number}</b></div>
                        <div>العميل: <b>{data.last_order.customer_name || "—"}</b></div>
                        <div>الإجمالي: <b className="num">{fmt(data.last_order.total_amount)} ر.س</b></div>
                        <div className="text-xs text-slate-500">استُلم: {fmtDateTime(data.last_order.received_at)}</div>
                    </div>
                </section>
            )}

            <section className="bg-white border border-slate-200 rounded-xl">
                <div className="p-4 border-b border-slate-100 flex items-center gap-2">
                    <FileText size={18} weight="duotone" className="text-violet-700" />
                    <h3 className="font-bold text-base text-slate-900">سجل أحدث 20 حدث</h3>
                </div>
                {data.recent_events.length === 0 ? (
                    <div className="p-8 text-center text-slate-500 text-sm">لا توجد أحداث بعد</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-50 text-slate-600 text-xs">
                                <tr>
                                    <th className="text-right p-3 font-bold">الوقت</th>
                                    <th className="text-right p-3 font-bold">النوع</th>
                                    <th className="text-right p-3 font-bold">الحالة</th>
                                    <th className="text-right p-3 font-bold">الملخص</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.recent_events.map((ev) => {
                                    const s = STATUS_BADGE[ev.status] || STATUS_BADGE.success;
                                    const Icon = s.icon;
                                    return (
                                        <tr key={ev.id} className="border-t border-slate-100" data-testid={`capi-event-${ev.id}`}>
                                            <td className="p-3 text-xs text-slate-600 whitespace-nowrap">{fmtDateTime(ev.created_at)}</td>
                                            <td className="p-3 text-xs font-bold">{EVENT_LABEL[ev.event_type] || ev.event_type}</td>
                                            <td className="p-3"><span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold border ${s.tone}`}><Icon size={12} /> {s.label}</span></td>
                                            <td className="p-3 text-xs text-slate-700">{ev.summary}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </section>

            {data.recent_errors.length > 0 && (
                <section className="bg-rose-50 border border-rose-200 rounded-xl p-4">
                    <h3 className="font-bold text-base text-rose-900 mb-2 flex items-center gap-2">
                        <Warning size={18} weight="duotone" /> آخر {data.recent_errors.length} خطأ
                    </h3>
                    <ul className="space-y-2 text-xs">
                        {data.recent_errors.map((e) => (
                            <li key={e.id} className="bg-white rounded p-3 border border-rose-100" data-testid={`capi-error-${e.id}`}>
                                <div className="flex justify-between mb-1">
                                    <b>{EVENT_LABEL[e.event_type] || e.event_type}</b>
                                    <span className="text-rose-700">{fmtDateTime(e.created_at)}</span>
                                </div>
                                <div className="text-rose-900">{e.summary}</div>
                                {e.error && <div className="text-rose-700 mt-1 font-mono text-[10px]">{e.error}</div>}
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}


function KPI({ title, value, sub, tone = "slate", Icon, testid }) {
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
            <div className="num text-2xl font-extrabold text-slate-900">{fmt(value)}</div>
            {sub && <div className="text-[11px] text-slate-500 mt-1">{sub}</div>}
        </div>
    );
}


// ── Main page ───────────────────────────────────────────────────────
export default function CustomAppIntegration() {
    const [tab, setTab] = useState("settings");
    const [settings, setSettings] = useState(null);
    const [loading, setLoading] = useState(true);

    const loadSettings = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/integrations/custom-app/settings");
            setSettings(data);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setLoading(false); }
    };
    useEffect(() => { loadSettings(); }, []);

    return (
        <div dir="rtl" data-testid="custom-app-integration-page" className="space-y-5">
            <div>
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                    <Plug size={28} weight="duotone" className="text-violet-700" />
                    ربط تطبيقي الخاص
                </h1>
                <p className="text-sm text-slate-500 mt-1">
                    استقبال الطلبات والمنتجات والعملاء من نظامك الخاص مباشرةً. مصدر إضافي يعمل بجانب Excel و Make.com و سلة بدون استبدالها.
                </p>
            </div>

            <div className="flex gap-1 border-b border-slate-200">
                {[
                    { v: "settings",   label: "الإعدادات",        testid: "capi-tab-settings" },
                    { v: "monitoring", label: "المراقبة والسجل", testid: "capi-tab-monitoring" },
                ].map((b) => (
                    <button key={b.v} onClick={() => setTab(b.v)} data-testid={b.testid}
                        className={`px-4 py-2 text-sm font-bold border-b-2 transition ${tab === b.v ? "border-violet-700 text-violet-900" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
                        {b.label}
                    </button>
                ))}
            </div>

            {tab === "settings" && (
                loading ? (
                    <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div>
                ) : <SettingsTab settings={settings} onChanged={setSettings} />
            )}
            {tab === "monitoring" && <MonitoringTab />}
        </div>
    );
}
