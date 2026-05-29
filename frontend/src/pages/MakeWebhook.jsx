import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    Plug, Copy, ArrowsClockwise, Trash, Lightning, CheckCircle, Warning,
    Database, Calendar, Download, ArrowSquareOut,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { formatMoney, todayISO } from "../lib/format";

function firstOfMonth() {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}

export default function MakeWebhook() {
    const [settings, setSettings] = useState(null);
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [rotating, setRotating] = useState(false);
    const [building, setBuilding] = useState(false);
    const [showSample, setShowSample] = useState(false);
    const [recent, setRecent] = useState([]);

    const [form, setForm] = useState({
        name: "",
        date_from: firstOfMonth(),
        date_to: todayISO(),
        snapchat_ads: "",
        tiktok_ads: "",
        instagram_ads: "",
        product_costs: "",
    });

    const loadAll = async () => {
        setLoading(true);
        try {
            const [s, st, rec] = await Promise.all([
                api.get("/webhook/settings"),
                api.get("/webhook/stats"),
                api.get("/webhook/orders?limit=10"),
            ]);
            setSettings(s.data);
            setStats(st.data);
            setRecent(rec.data.orders || []);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setLoading(false); }
    };

    useEffect(() => { loadAll(); }, []);

    const copy = async (text, label = "تم النسخ") => {
        try {
            await navigator.clipboard.writeText(text);
            toast.success(label);
        } catch {
            toast.error("تعذّر النسخ من المتصفح");
        }
    };

    const rotate = async () => {
        if (!window.confirm("سيتم إبطال الـ Token الحالي وستحتاج إلى تحديث Make.com بـ URL الجديد. متابعة؟")) return;
        setRotating(true);
        try {
            await api.post("/webhook/settings/rotate-token");
            toast.success("تم توليد Token جديد");
            await loadAll();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setRotating(false); }
    };

    const disconnect = async () => {
        if (!window.confirm("سيتم حذف الـ Token وكل الطلبات المخزّنة من Make.com. متابعة؟")) return;
        try {
            await api.delete("/webhook/settings");
            toast.success("تم فصل Make.com وحذف الطلبات");
            await loadAll();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    const buildAnalysis = async () => {
        if (!form.date_from || !form.date_to) {
            toast.error("اختر تاريخ البداية والنهاية");
            return;
        }
        setBuilding(true);
        try {
            const { data } = await api.post("/webhook/build-analysis", {
                name: form.name || "",
                date_from: form.date_from,
                date_to: form.date_to,
                snapchat_ads: Number(form.snapchat_ads || 0),
                tiktok_ads: Number(form.tiktok_ads || 0),
                instagram_ads: Number(form.instagram_ads || 0),
                product_costs: Number(form.product_costs || 0),
            });
            toast.success("تم إنشاء التحليل من بيانات Make.com");
            window.location.href = `/analysis/${data.id}`;
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setBuilding(false); }
    };

    if (loading || !settings) {
        return (
            <div className="space-y-4">
                <div className="h-24 rounded-xl bg-white border border-border animate-pulse" />
                <div className="h-48 rounded-xl bg-white border border-border animate-pulse" />
            </div>
        );
    }

    const isConnected = (stats?.total_received_ever || 0) > 0;

    return (
        <div className="space-y-6" data-testid="make-webhook-page">
            {/* Header */}
            <div className="flex items-start justify-between gap-3 flex-col md:flex-row">
                <div>
                    <h1 className="text-3xl md:text-4xl font-bold text-foreground flex items-center gap-2" style={{ fontFamily: "Tajawal" }}>
                        <Plug size={32} weight="duotone" />
                        ربط Make.com
                    </h1>
                    <p className="text-sm text-muted-foreground mt-2 max-w-2xl">
                        مصدر بيانات بديل لرفع Excel — استقبل الطلبات تلقائياً من Make.com (التي تأخذها من سلة)، ثم أنشئ تحليلاً بنفس مخرجات Excel ونفس التقارير.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {isConnected ? (
                        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-700" data-testid="make-status-connected">
                            <CheckCircle size={14} weight="fill" /> مفعّل
                        </span>
                    ) : (
                        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold bg-gray-100 text-gray-700" data-testid="make-status-pending">
                            <Warning size={14} weight="fill" /> في انتظار أول طلب
                        </span>
                    )}
                </div>
            </div>

            {/* Stats strip */}
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
                <StatCard icon={Database} label="إجمالي الطلبات المخزّنة" value={stats?.total_orders_in_db || 0} testid="stat-stored" />
                <StatCard icon={Lightning} label="إجمالي المستقبَل" value={stats?.total_received_ever || 0} testid="stat-received" />
                <StatCard icon={Calendar} label="آخر مزامنة" value={formatDateTime(stats?.last_sync_at)} testid="stat-last-sync" small />
                <StatCard
                    icon={Calendar}
                    label="نطاق الطلبات"
                    value={stats?.date_range ? `${stats.date_range.earliest} → ${stats.date_range.latest}` : "—"}
                    testid="stat-range"
                    small
                />
            </div>

            {/* Webhook URL */}
            <div className="rounded-xl border border-border bg-white p-6 space-y-4">
                <h2 className="text-xl font-bold flex items-center gap-2">
                    <Lightning size={22} weight="bold" /> Webhook URL
                </h2>

                <div>
                    <label className="block text-xs font-bold text-muted-foreground mb-1.5">
                        ضع هذا الرابط في وحدة "HTTP → Make a request" داخل سيناريو Make.com
                    </label>
                    <div className="flex items-center gap-2">
                        <input
                            readOnly
                            value={settings.webhook_url}
                            className="flex-1 px-3 py-2.5 text-sm border border-border rounded-lg bg-accent/40 font-mono"
                            dir="ltr"
                            data-testid="make-webhook-url"
                            onClick={(e) => e.target.select()}
                        />
                        <button
                            type="button"
                            onClick={() => copy(settings.webhook_url, "تم نسخ الرابط")}
                            className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors"
                            data-testid="copy-url-btn"
                        >
                            <Copy size={16} weight="bold" /> نسخ
                        </button>
                    </div>
                </div>

                <div>
                    <label className="block text-xs font-bold text-muted-foreground mb-1.5">Token (مدمج في الرابط)</label>
                    <div className="flex items-center gap-2">
                        <code className="flex-1 px-3 py-2.5 text-sm border border-border rounded-lg bg-accent/40 font-mono" dir="ltr" data-testid="make-token">
                            {settings.token}
                        </code>
                        <button
                            type="button"
                            onClick={rotate}
                            disabled={rotating}
                            className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors disabled:opacity-60"
                            data-testid="rotate-token-btn"
                        >
                            <ArrowsClockwise size={16} weight="bold" />
                            {rotating ? "جاري…" : "توليد جديد"}
                        </button>
                    </div>
                </div>

                <div className="flex items-center gap-2 pt-2">
                    <button
                        type="button"
                        onClick={() => setShowSample(!showSample)}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-xs font-semibold hover:bg-accent transition-colors"
                        data-testid="toggle-sample-btn"
                    >
                        <ArrowSquareOut size={14} weight="bold" />
                        {showSample ? "إخفاء" : "عرض"} مثال JSON
                    </button>
                    {(stats?.total_received_ever || 0) > 0 && (
                        <button
                            type="button"
                            onClick={disconnect}
                            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-xs font-semibold text-red-600 hover:bg-red-50 hover:border-red-200 transition-colors"
                            data-testid="disconnect-btn"
                        >
                            <Trash size={14} weight="bold" /> فصل وحذف الطلبات
                        </button>
                    )}
                </div>

                {showSample && (
                    <div className="mt-2 rounded-lg bg-slate-900 text-emerald-300 p-4 overflow-x-auto" data-testid="sample-json">
                        <pre className="text-xs font-mono whitespace-pre" dir="ltr">{JSON.stringify(settings.sample_payload, null, 2)}</pre>
                        <p className="text-xs text-emerald-100 mt-3 leading-relaxed" dir="rtl">
                            • أرسل كائناً واحداً أو مصفوفة <code className="bg-slate-800 px-1 rounded">[...]</code> أو
                            <code className="bg-slate-800 px-1 rounded ms-1">{`{"orders":[...]}`}</code>.<br />
                            • مفتاح المنع من التكرار: <code className="bg-slate-800 px-1 rounded">order_number</code>. إذا تكرر سيتم تحديثه بدل الإضافة.<br />
                            • أسماء طرق الدفع وشركات الشحن ستُطابَق مع إعداداتك تلقائياً.
                        </p>
                    </div>
                )}
            </div>

            {/* Build analysis */}
            <div className="rounded-xl border border-border bg-white p-6 space-y-4" data-testid="build-analysis-section">
                <h2 className="text-xl font-bold flex items-center gap-2">
                    <Download size={22} weight="bold" /> إنشاء تحليل من بيانات Make.com
                </h2>
                <p className="text-sm text-muted-foreground">
                    اختر الفترة وأدخل تكاليف الإعلانات والمنتجات؛ سيتم إنشاء تقرير بنفس مخرجات تحليل Excel ويُضاف إلى سجل التحليلات.
                </p>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <Field label="من تاريخ" type="date" value={form.date_from} onChange={(v) => setForm({ ...form, date_from: v })} testid="build-date-from" />
                    <Field label="إلى تاريخ" type="date" value={form.date_to} onChange={(v) => setForm({ ...form, date_to: v })} testid="build-date-to" />
                    <Field label="اسم التحليل (اختياري)" value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="تحليل فبراير 2026" testid="build-name" />
                    <Field label="إعلانات سناب (ر.س)" type="number" value={form.snapchat_ads} onChange={(v) => setForm({ ...form, snapchat_ads: v })} placeholder="0" testid="build-snap" />
                    <Field label="إعلانات تيك توك (ر.س)" type="number" value={form.tiktok_ads} onChange={(v) => setForm({ ...form, tiktok_ads: v })} placeholder="0" testid="build-tiktok" />
                    <Field label="إعلانات إنستقرام (ر.س)" type="number" value={form.instagram_ads} onChange={(v) => setForm({ ...form, instagram_ads: v })} placeholder="0" testid="build-insta" />
                    <Field label="تكاليف المنتجات (ر.س)" type="number" value={form.product_costs} onChange={(v) => setForm({ ...form, product_costs: v })} placeholder="0" testid="build-products" />
                </div>

                <div className="flex items-center justify-end">
                    <button
                        type="button"
                        onClick={buildAnalysis}
                        disabled={building || (stats?.total_orders_in_db || 0) === 0}
                        title={(stats?.total_orders_in_db || 0) === 0 ? "لا توجد طلبات مخزّنة بعد" : ""}
                        className="inline-flex items-center gap-2 px-5 py-3 rounded-lg bg-brand text-white text-sm font-bold hover:opacity-90 transition-opacity disabled:opacity-50"
                        data-testid="build-analysis-btn"
                    >
                        <Download size={18} weight="bold" />
                        {building ? "جاري الإنشاء…" : "إنشاء تحليل"}
                    </button>
                </div>
            </div>

            {/* Recent orders */}
            <div className="rounded-xl border border-border bg-white p-6" data-testid="recent-orders-section">
                <div className="flex items-center justify-between mb-3">
                    <h2 className="text-xl font-bold">آخر الطلبات المستلمة</h2>
                    <Link
                        to="/history"
                        className="text-sm text-brand font-semibold hover:underline inline-flex items-center gap-1"
                    >
                        سجل التحليلات <ArrowSquareOut size={14} />
                    </Link>
                </div>
                {recent.length === 0 ? (
                    <p className="text-center py-10 text-sm text-muted-foreground">لم تصلنا أي طلبات بعد. أرسل أول طلب من Make.com وستظهر هنا.</p>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-xs font-bold text-muted-foreground uppercase border-b border-border">
                                    <th className="text-start py-2 px-2">رقم الطلب</th>
                                    <th className="text-start py-2 px-2">التاريخ</th>
                                    <th className="text-end py-2 px-2">المجموع</th>
                                    <th className="text-start py-2 px-2">الدفع</th>
                                    <th className="text-start py-2 px-2">حالة الدفع</th>
                                    <th className="text-start py-2 px-2">الشحن</th>
                                    <th className="text-start py-2 px-2">العميل</th>
                                    <th className="text-start py-2 px-2">الجوال</th>
                                </tr>
                            </thead>
                            <tbody>
                                {recent.map((o) => (
                                    <tr key={o.order_number} className="border-b border-border last:border-0 hover:bg-accent/30">
                                        <td className="py-2 px-2 font-mono font-bold" dir="ltr">{o.order_number}</td>
                                        <td className="py-2 px-2" dir="ltr">{o.order_date || "—"}</td>
                                        <td className="py-2 px-2 text-end font-bold num">
                                            {formatMoney(o.total ?? o.total_amount)} {o.currency || "ر.س"}
                                        </td>
                                        <td className="py-2 px-2">{o.payment_method || "—"}</td>
                                        <td className="py-2 px-2">
                                            {o.payment_status ? (
                                                <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${o.payment_status.toLowerCase() === "paid" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                                                    {o.payment_status}
                                                </span>
                                            ) : "—"}
                                        </td>
                                        <td className="py-2 px-2">{o.shipping_company || "—"}</td>
                                        <td className="py-2 px-2 text-muted-foreground">{o.customer_name || "—"}</td>
                                        <td className="py-2 px-2 text-muted-foreground" dir="ltr">{o.customer_mobile || "—"}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

function StatCard({ icon: Icon, label, value, testid, small = false }) {
    return (
        <div className="rounded-xl border border-border bg-white p-4" data-testid={testid}>
            <div className="flex items-center gap-2 text-muted-foreground text-xs font-semibold mb-1.5">
                <Icon size={16} weight="duotone" /> {label}
            </div>
            <div className={`font-bold ${small ? "text-sm" : "text-2xl num"}`} dir={small ? "ltr" : "rtl"}>
                {value || "—"}
            </div>
        </div>
    );
}

function Field({ label, value, onChange, type = "text", placeholder = "", testid }) {
    return (
        <div>
            <label className="block text-xs font-bold text-muted-foreground mb-1.5">{label}</label>
            <input
                type={type}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                placeholder={placeholder}
                className={`w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand ${type === "number" ? "num" : ""}`}
                data-testid={testid}
                dir={type === "number" || type === "date" ? "ltr" : "rtl"}
            />
        </div>
    );
}

function formatDateTime(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleString("ar-SA", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch {
        return iso;
    }
}
