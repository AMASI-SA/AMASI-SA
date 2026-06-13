import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    Plug, Copy, ArrowsClockwise, Trash, Lightning, CheckCircle, Warning,
    Database, Calendar, Download, ArrowSquareOut,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { formatMoney, todayISO } from "../lib/format";
import DateInput from "../components/DateInput";

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

    const [testing, setTesting] = useState(false);
    const testWebhook = async () => {
        if (!settings?.webhook_url) {
            toast.error("لا يوجد Token مُولَّد بعد");
            return;
        }
        // Extract token from the URL (last path segment)
        const tok = String(settings.webhook_url).split("/").pop();
        setTesting(true);
        try {
            // Hit the SAME public URL that Make.com would call, so we
            // test the network path end-to-end (not just the database).
            const base = settings.webhook_url.replace(/\/api\/.*$/, "");
            const u = `${base}/api/webhook/ping/${encodeURIComponent(tok)}`;
            const resp = await fetch(u, { method: "POST" });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.ok) {
                toast.success(
                    `✓ الرابط يعمل في هذه البيئة: ${data.environment}`,
                    { duration: 6000 }
                );
            } else {
                const reason = data?.detail?.hint || data?.detail?.reason || `HTTP ${resp.status}`;
                toast.error(`✗ ${reason}`, { duration: 8000 });
            }
        } catch (e) {
            toast.error(`تعذّر الاتصال: ${e.message}`);
        } finally { setTesting(false); }
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
                <StatCard icon={Database} label="إجمالي الطلبات الموحَّدة" value={stats?.total_orders_in_db || 0} testid="stat-stored" />
                <StatCard
                    icon={Lightning}
                    label="من Make / Excel"
                    value={`${stats?.by_source?.make || 0} / ${stats?.by_source?.excel || 0}`}
                    testid="stat-by-source"
                    small
                />
                <StatCard icon={Calendar} label="آخر مزامنة" value={formatDateTime(stats?.last_sync_at)} testid="stat-last-sync" small />
                <StatCard
                    icon={Calendar}
                    label="نطاق الطلبات"
                    value={stats?.date_range ? `${stats.date_range.earliest} → ${stats.date_range.latest}` : "—"}
                    testid="stat-range"
                    small
                />
            </div>

            {/* Info: orders with inferred date (Make.com missing created_at) */}
            {(stats?.orders_inferred_date || 0) > 0 && (
                <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 flex items-start gap-3" data-testid="inferred-date-banner">
                    <Warning size={28} weight="fill" className="text-amber-600 shrink-0 mt-0.5" />
                    <div className="flex-1">
                        <div className="font-bold text-amber-900">
                            ℹ️ يوجد <strong>{stats.orders_inferred_date}</strong> طلب بتاريخ تقريبي
                        </div>
                        <div className="text-sm text-amber-800 mt-1 leading-relaxed">
                            وصلت هذه الطلبات من Make.com بدون حقل <code className="bg-amber-100 px-1 py-0.5 rounded">created_at</code>،
                            فاستُخدم تاريخ الاستلام كتاريخ تقريبي لتظهر في لوحة التحكم. <strong>قد لا تكون في شهرها الصحيح.</strong>
                            <br />
                            <strong>للحصول على التاريخ الدقيق:</strong> في سيناريو Make.com، أضف حقل <code className="bg-amber-100 px-1 py-0.5 rounded">created_at</code> من Salla في الـ JSON body،
                            ثم أعد إرسال الطلبات — سيتم تصحيح التاريخ تلقائياً.
                        </div>
                    </div>
                </div>
            )}
            {/* Warning: orders that have no date at all (extremely rare now) */}
            {(stats?.orders_missing_date || 0) > 0 && (
                <div className="rounded-xl border border-red-300 bg-red-50 p-4 flex items-start gap-3" data-testid="missing-date-banner">
                    <Warning size={28} weight="fill" className="text-red-600 shrink-0 mt-0.5" />
                    <div className="flex-1">
                        <div className="font-bold text-red-900">
                            ⚠️ يوجد <strong>{stats.orders_missing_date}</strong> طلب بدون تاريخ
                        </div>
                        <div className="text-sm text-red-800 mt-1 leading-relaxed">
                            هذه طلبات قديمة محفوظة بدون أي تاريخ ولن تظهر في لوحة التحكم.
                            يرجى التواصل مع الدعم لإصلاحها.
                        </div>
                    </div>
                </div>
            )}

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
                        <button
                            type="button"
                            onClick={testWebhook}
                            disabled={testing}
                            className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg bg-sky-700 text-white text-sm font-semibold hover:bg-sky-800 disabled:opacity-50 transition-colors"
                            data-testid="test-webhook-btn"
                            title="يستدعي رابط الـ webhook من المتصفح ويتحقّق من القبول في هذه البيئة"
                        >
                            {testing ? "جارٍ الاختبار…" : "اختبر الآن"}
                        </button>
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-2" data-testid="webhook-env-hint">
                        🔒 هذا الرابط يعمل فقط على البيئة التي أُنشئ فيها.
                        إذا غيّرت الدومين (مثل التحويل بين <code className="bg-slate-100 px-1 rounded">المعاينة</code> و
                        <code className="bg-slate-100 px-1 rounded">الإنتاج</code>) فيلزم توليد رمز جديد وتحديث Make.com.
                    </p>
                </div>

                {/* TikTok Ads webhook (uses same token, different path) */}
                {settings.tiktok_webhook_url && (
                    <div className="border-t border-border pt-5">
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">
                            🎵 رابط استقبال TikTok Ads (نفس Token — لإحصائيات الإعلانات اليومية)
                        </label>
                        <div className="flex items-center gap-2">
                            <input
                                readOnly
                                value={settings.tiktok_webhook_url}
                                className="flex-1 px-3 py-2.5 text-sm border border-pink-200 rounded-lg bg-pink-50/40 font-mono"
                                dir="ltr"
                                data-testid="tiktok-webhook-url"
                                onClick={(e) => e.target.select()}
                            />
                            <button
                                type="button"
                                onClick={() => copy(settings.tiktok_webhook_url, "تم نسخ رابط TikTok")}
                                className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors"
                                data-testid="copy-tiktok-url-btn"
                            >
                                <Copy size={16} weight="bold" /> نسخ
                            </button>
                        </div>
                        <p className="text-xs text-muted-foreground mt-2">
                            مثال JSON: <code dir="ltr" className="bg-accent/40 px-1 rounded">{'{"source":"tiktok","date":"2026-05-30","spend":350.75,"purchases":12,"revenue":2400}'}</code>
                        </p>
                    </div>
                )}

                {/* Meta Ads webhook (Facebook + Instagram) — same token, different path */}
                {settings.token && (
                    <div className="border-t border-border pt-5">
                        <label className="block text-xs font-bold text-muted-foreground mb-1.5">
                            📘 رابط استقبال Meta Ads (Facebook + Instagram — نفس Token)
                        </label>
                        <div className="flex items-center gap-2">
                            <input
                                readOnly
                                value={`${window.location.origin}/api/webhook/meta/${settings.token}`}
                                className="flex-1 px-3 py-2.5 text-sm border border-blue-200 rounded-lg bg-blue-50/40 font-mono"
                                dir="ltr"
                                data-testid="meta-webhook-url"
                                onClick={(e) => e.target.select()}
                            />
                            <button
                                type="button"
                                onClick={() => copy(`${window.location.origin}/api/webhook/meta/${settings.token}`, "تم نسخ رابط Meta")}
                                className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg border border-border text-sm font-semibold hover:bg-accent transition-colors"
                                data-testid="copy-meta-url-btn"
                            >
                                <Copy size={16} weight="bold" /> نسخ
                            </button>
                        </div>
                        <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
                            في Make.com: أنشئ سيناريو "Facebook Ads → Get Insights" يومياً 01:00 صباحاً، وأرسل لكل حملة بصيغة:
                        </p>
                        <code dir="ltr" className="block mt-1 text-[10px] bg-accent/40 px-2 py-1.5 rounded leading-relaxed">
                            {'{"platform":"meta","date":"2026-05-31","account_id":"act_XXX","campaign_id":"cmp_XXX","campaign_name":"...","spend":350.75,"impressions":12000,"clicks":250,"cpc":1.40,"cpm":29.20,"ctr":2.08,"purchases":8,"purchase_value":1200.50}'}
                        </code>
                    </div>
                )}

                <div>
                    <label className="block text-xs font-bold text-muted-foreground mb-1.5">Token (مدمج في الرابط)</label>
                    <div className="flex items-center gap-2">
                        <code className="flex-1 px-3 py-2.5 text-sm border border-border rounded-lg bg-accent/40 font-mono" dir="ltr" data-testid="make-token">
                            {settings.token}                        </code>
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
                    <h2 className="text-xl font-bold">آخر الطلبات الموحَّدة (Excel + Make.com)</h2>
                    <Link
                        to="/history"
                        className="text-sm text-brand font-semibold hover:underline inline-flex items-center gap-1"
                    >
                        سجل التحليلات <ArrowSquareOut size={14} />
                    </Link>
                </div>
                {recent.length === 0 ? (
                    <p className="text-center py-10 text-sm text-muted-foreground">
                        لم تصلنا أي طلبات بعد. ارفع ملف Excel من القائمة الجانبية أو أرسل أول طلب من Make.com.
                    </p>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="mezan-table w-full text-sm">
                            <thead>
                                <tr className="text-xs font-bold text-muted-foreground uppercase border-b border-border">
                                    <th className="text-start py-2 px-2">رقم الطلب</th>
                                    <th className="text-start py-2 px-2">المصدر</th>
                                    <th className="text-start py-2 px-2">التاريخ</th>
                                    <th className="text-end py-2 px-2">المجموع</th>
                                    <th className="text-start py-2 px-2">الدفع</th>
                                    <th className="text-start py-2 px-2">حالة الطلب</th>
                                    <th className="text-start py-2 px-2">الشحن</th>
                                    <th className="text-start py-2 px-2">العميل</th>
                                    <th className="text-start py-2 px-2">الجوال</th>
                                </tr>
                            </thead>
                            <tbody>
                                {recent.map((o) => {
                                    const amount = Number(o.total_amount || o.total || 0);
                                    const displayDate = o.order_date || (o.received_at ? o.received_at.slice(0, 10) : "");
                                    const ds = o.data_source || "—";
                                    const dsLabel = ds === "make" ? "Make" : ds === "excel" ? "Excel" : ds;
                                    const dsColor = ds === "make"
                                        ? "bg-yellow-100 text-yellow-700"
                                        : ds === "excel"
                                        ? "bg-emerald-100 text-emerald-700"
                                        : "bg-gray-100 text-gray-700";
                                    // Show "merged" indicator if order touched by both sources
                                    const sourcesSet = new Set((o.data_sources || []).map((s) => s.source));
                                    const isMerged = sourcesSet.size > 1;
                                    return (
                                        <tr key={o.order_number} className="border-b border-border last:border-0 hover:bg-accent/30">
                                            <td className="py-2 px-2 font-mono font-bold" dir="ltr">{o.order_number || "—"}</td>
                                            <td className="py-2 px-2">
                                                <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${dsColor}`}>
                                                    {dsLabel}
                                                </span>
                                                {isMerged && (
                                                    <span className="ms-1 inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-sky-100 text-sky-700" title="بيانات من Excel و Make">
                                                        مدمج
                                                    </span>
                                                )}
                                            </td>
                                            <td className="py-2 px-2" dir="ltr">{displayDate || "—"}</td>
                                            <td className="py-2 px-2 text-end font-bold num">
                                                {amount > 0 ? `${formatMoney(amount)} ${o.currency || "ر.س"}` : "—"}
                                            </td>
                                            <td className="py-2 px-2">{o.payment_method || "—"}</td>
                                            <td className="py-2 px-2">
                                                {o.order_status ? (
                                                    <span className="inline-block px-2 py-0.5 rounded text-xs font-bold bg-indigo-100 text-indigo-700">
                                                        {o.order_status}
                                                    </span>
                                                ) : "—"}
                                            </td>
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
                                    );
                                })}
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
    if (type === "date") {
        return (
            <div>
                <label className="block text-xs font-bold text-muted-foreground mb-1.5">{label}</label>
                <DateInput
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    data-testid={testid}
                />
            </div>
        );
    }
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
                dir={type === "number" ? "ltr" : "rtl"}
            />
        </div>
    );
}

function formatDateTime(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleString("en-US", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch {
        return iso;
    }
}
