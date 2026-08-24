import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
    Storefront, CheckCircle, Warning, ArrowsClockwise, LinkBreak,
    PlugsConnected, ShieldCheck, Info, ArrowSquareOut, Lock,
    Globe, EnvelopeSimple, Hash, Clock, Package, ChartPieSlice,
    PencilSimple,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";

function riyadhTodayISO() {
    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).formatToParts(new Date());
    const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${byType.year}-${byType.month}-${byType.day}`;
}

/**
 * /settings/salla — "ربط متجر سلة"
 *
 * Shows connection health, direct data sync, live abandoned-cart delivery,
 * and the four privacy-safe abandoned-cart webhook states.
 *
 * IMPORTANT: This page DOES NOT touch any other data source. Make, PDF
 * upload, and Excel upload all continue to work exactly as before.
 */

function StatusPill({ status, configured }) {
    if (!configured) {
        return (
            <span
                className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-slate-100 text-slate-600 text-xs font-bold border border-slate-200"
                data-testid="salla-status-pill"
            >
                <Lock size={10} weight="bold" /> غير مُعدّ بعد
            </span>
        );
    }
    const map = {
        connected: { bg: "bg-emerald-50 border-emerald-200 text-emerald-700", icon: CheckCircle, label: "متصل" },
        not_connected: { bg: "bg-slate-100 border-slate-200 text-slate-600", icon: PlugsConnected, label: "غير مربوط" },
        needs_reauth: { bg: "bg-amber-50 border-amber-200 text-amber-800", icon: Warning, label: "انتهت صلاحية الاتصال — أعد الربط" },
        unknown: { bg: "bg-slate-100 border-slate-200 text-slate-600", icon: Info, label: "حالة غير معروفة" },
    };
    const m = map[status] || map.unknown;
    const Icon = m.icon;
    return (
        <span
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-bold border ${m.bg}`}
            data-testid="salla-status-pill"
        >
            <Icon size={10} weight="bold" /> {m.label}
        </span>
    );
}

function InfoRow({ icon: Icon, label, value, testid, mono = false }) {
    return (
        <div className="flex items-center gap-2 py-1.5 border-b last:border-b-0 border-slate-100">
            <Icon size={14} weight="bold" className="text-slate-400 flex-shrink-0" />
            <span className="text-xs font-bold text-slate-500 flex-shrink-0 w-24">{label}</span>
            <span
                className={`text-sm text-slate-800 truncate ${mono ? "font-mono" : "font-bold"}`}
                title={String(value ?? "")}
                data-testid={testid}
            >
                {value || <span className="text-slate-400 font-normal">—</span>}
            </span>
        </div>
    );
}

function ConfirmModal({ open, title, description, confirmLabel, onConfirm, onCancel, danger }) {
    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4" data-testid="salla-confirm-modal">
            <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6" style={{ fontFamily: "Tajawal" }}>
                <div className="flex items-start gap-3 mb-4">
                    <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${danger ? "bg-rose-100 text-rose-600" : "bg-indigo-100 text-indigo-600"}`}>
                        <Warning size={22} weight="fill" />
                    </div>
                    <div className="flex-1">
                        <h3 className="font-extrabold text-lg text-slate-900">{title}</h3>
                        <p className="text-sm text-slate-600 mt-1 leading-relaxed whitespace-pre-line">{description}</p>
                    </div>
                </div>
                <div className="flex gap-2 justify-end">
                    <button type="button" onClick={onCancel} className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 font-bold hover:bg-slate-200" data-testid="salla-confirm-cancel-btn">إلغاء</button>
                    <button type="button" onClick={onConfirm} className={`px-4 py-2 rounded-lg font-bold text-white ${danger ? "bg-rose-600 hover:bg-rose-700" : "bg-indigo-600 hover:bg-indigo-700"}`} data-testid="salla-confirm-ok-btn">{confirmLabel}</button>
                </div>
            </div>
        </div>
    );
}

export default function SallaIntegration() {
    const location = useLocation();
    const navigate = useNavigate();
    const [loading, setLoading] = useState(true);
    const [status, setStatus] = useState(null);
    const [config, setConfig] = useState(null);
    const [busy, setBusy] = useState({ connect: false, test: false, refresh: false, disconnect: false, saveConfig: false, syncOrders: false, syncProducts: false, reset: false });
    const [showDisconnect, setShowDisconnect] = useState(false);
    const [showReset, setShowReset] = useState(false);
    const [liveStoreInfo, setLiveStoreInfo] = useState(null);
    const [showConfigForm, setShowConfigForm] = useState(false);
    const [cfgInputs, setCfgInputs] = useState({ client_id: "", client_secret: "", redirect_uri: "" });
    const [syncLogs, setSyncLogs] = useState([]);
    const [cartStatus, setCartStatus] = useState(null);
    const [webhookMonitor, setWebhookMonitor] = useState(null);
    const [syncToDate, setSyncToDate] = useState(riyadhTodayISO);
    const hasRunningOrderSync = syncLogs.some(
        (log) => log.kind === "orders"
            && log.status === "running"
            && Number.isFinite(Date.parse(log.started_at))
            && Date.now() - Date.parse(log.started_at) < 30 * 60 * 1_000
    );

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [statusRes, cfgRes, logsRes] = await Promise.all([
                api.get("/salla/status"),
                api.get("/salla/config"),
                api.get("/salla/sync/logs?limit=20"),
            ]);
            setStatus(statusRes.data);
            setConfig(cfgRes.data);
            setSyncLogs(logsRes.data?.logs || []);
            if (statusRes.data?.connected) {
                const [cartResult, webhookResult] = await Promise.allSettled([
                    api.get("/salla/abandoned-carts/status"),
                    api.get("/salla/webhook-monitor"),
                ]);
                if (cartResult.status === "fulfilled") {
                    setCartStatus(cartResult.value.data);
                }
                if (webhookResult.status === "fulfilled") {
                    setWebhookMonitor(webhookResult.value.data);
                }
            } else {
                setCartStatus(null);
                setWebhookMonitor(null);
            }
            setCfgInputs((prev) => ({
                client_id: cfgRes.data?.client_id || prev.client_id,
                client_secret: "",
                redirect_uri: cfgRes.data?.redirect_uri || prev.redirect_uri,
            }));
        } catch (e) {
            toast.error(e?.response?.data?.detail || "تعذّر تحميل حالة الربط");
        } finally {
            setLoading(false);
        }
    }, []);

    // Handle callback-bounce: Salla → /api/salla/oauth/callback → here.
    useEffect(() => {
        const sp = new URLSearchParams(location.search);
        const s = sp.get("status");
        if (s === "connected") {
            toast.success("تم ربط متجر سلة بنجاح ✅");
            navigate({ pathname: location.pathname, search: "" }, { replace: true });
        } else if (s === "error") {
            const reason = sp.get("reason") || "unknown";
            const detail = sp.get("detail") || "";
            toast.error(`فشل الربط: ${reason}${detail ? ` — ${detail}` : ""}`);
            navigate({ pathname: location.pathname, search: "" }, { replace: true });
        } else if (s === "warn") {
            const reason = sp.get("reason") || "warning";
            toast.warning(`الربط نجح لكن: ${reason}`);
            navigate({ pathname: location.pathname, search: "" }, { replace: true });
        }
    }, [location.search, location.pathname, navigate]);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        if (!hasRunningOrderSync) return undefined;
        const timer = window.setInterval(async () => {
            try {
                const { data } = await api.get("/salla/sync/logs?limit=20");
                setSyncLogs(data?.logs || []);
            } catch (_) {
                // Preserve the last progress snapshot; the next poll retries.
            }
        }, 5_000);
        return () => window.clearInterval(timer);
    }, [hasRunningOrderSync]);

    const handleConnect = async () => {
        setBusy(b => ({ ...b, connect: true }));
        try {
            // Iter-2026-02.rev23 — Easy Mode ONLY. No /oauth/login,
            // no accounts.salla.sa/oauth2/auth, no redirect_uri.
            // Backend reads SALLA_APP_ID from env and returns the exact
            // install URL. We open it in a new tab so the merchant's
            // Mezan session is preserved.
            const { data } = await api.get("/salla/config");
            if (data?.install_url_error === "SALLA_APP_ID_NOT_CONFIGURED"
                || !data?.install_url) {
                toast.error(
                    "SALLA_APP_ID_NOT_CONFIGURED — أضف SALLA_APP_ID في backend/.env على Production ثم أعد المحاولة.");
                return;
            }
            // Open Salla install page in a new tab. Salla will POST the
            // authorize event to our webhook after the merchant clicks
            // "Install" — no callback URL involved.
            window.open(data.install_url, "_blank", "noopener,noreferrer");
            toast.info(
                "افتُحت صفحة تثبيت التطبيق في سلة. بعد الضغط على «تثبيت» ستصلنا بيانات الربط تلقائياً.");
        } catch (e) {
            const detail = e?.response?.data?.detail || e.message || "فشل بدء الربط";
            toast.error(typeof detail === "string" ? detail : detail.message || "فشل بدء الربط");
        } finally {
            setBusy(b => ({ ...b, connect: false }));
        }
    };

    const handleTest = async () => {
        setBusy(b => ({ ...b, test: true }));
        try {
            const { data } = await api.post("/salla/test-connection");
            setLiveStoreInfo(data.store);
            toast.success(`الاتصال يعمل ✅ — متجر "${data.store?.name || ""}"`);
            await load();
        } catch (e) {
            const det = e?.response?.data?.detail;
            const msg = typeof det === "string" ? det : (det?.message || "فشل اختبار الاتصال");
            toast.error(msg);
        } finally {
            setBusy(b => ({ ...b, test: false }));
        }
    };

    const handleRefresh = async () => {
        setBusy(b => ({ ...b, refresh: true }));
        try {
            const { data } = await api.post("/salla/refresh-store-info");
            setLiveStoreInfo(data.store);
            toast.success("تم تحديث بيانات المتجر");
            await load();
        } catch (e) {
            const det = e?.response?.data?.detail;
            const msg = typeof det === "string" ? det : (det?.message || "فشل تحديث البيانات");
            toast.error(msg);
        } finally {
            setBusy(b => ({ ...b, refresh: false }));
        }
    };

    const handleDisconnect = async () => {
        setShowDisconnect(false);
        setBusy(b => ({ ...b, disconnect: true }));
        try {
            await api.post("/salla/disconnect");
            setLiveStoreInfo(null);
            toast.success("تم فصل متجر سلة. مصادر Make و PDF و Excel لم تتأثر.");
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الفصل");
        } finally {
            setBusy(b => ({ ...b, disconnect: false }));
        }
    };

    const handleSaveConfig = async () => {
        if (!cfgInputs.client_id.trim()) {
            toast.error("Client ID مطلوب");
            return;
        }
        setBusy(b => ({ ...b, saveConfig: true }));
        try {
            await api.put("/salla/config", {
                client_id: cfgInputs.client_id.trim(),
                client_secret: cfgInputs.client_secret.trim() || undefined,
                redirect_uri: cfgInputs.redirect_uri.trim(),
            });
            toast.success("تم حفظ بيانات تطبيق سلة. يمكنك الآن الضغط على ربط متجر سلة.");
            setShowConfigForm(false);
            setCfgInputs(prev => ({ ...prev, client_secret: "" }));
            await load();
        } catch (e) {
            const det = e?.response?.data?.detail;
            const msg = typeof det === "string" ? det : (det?.message || "فشل الحفظ");
            toast.error(msg);
        } finally {
            setBusy(b => ({ ...b, saveConfig: false }));
        }
    };

    const handleSyncOrders = async () => {
        setBusy(b => ({ ...b, syncOrders: true }));
        try {
            const { data } = await api.post("/salla/sync/orders", {
                updated_since_hours: 24 * 30, // last 30 days by default
            });
            toast.success(
                data.already_running
                    ? "مزامنة الطلبات تعمل بالفعل في الخلفية"
                    : "بدأت مزامنة الطلبات في الخلفية؛ سيُحدّث السجل تلقائيًا"
            );
            await load();
        } catch (e) {
            const det = e?.response?.data?.detail;
            const msg = typeof det === "string" ? det : (det?.message || "فشل مزامنة الطلبات");
            toast.error(msg);
            await load();
        } finally {
            setBusy(b => ({ ...b, syncOrders: false }));
        }
    };

    const handleSyncOrdersRange = async () => {
        const fixedFromDate = "2026-07-01";

        if (!syncToDate) {
            toast.error("حدد تاريخ النهاية أولاً");
            return;
        }

        if (syncToDate < fixedFromDate) {
            toast.error("تاريخ النهاية يجب ألا يكون قبل 2026-07-01");
            return;
        }

        setBusy(b => ({ ...b, syncOrders: true }));
        try {
            const { data } = await api.post("/salla/sync/orders", {
                from_date: fixedFromDate,
                to_date: syncToDate,
            });

            toast.success(
                data.already_running
                    ? "مزامنة الفترة تعمل بالفعل في الخلفية"
                    : "بدأت مزامنة الفترة في الخلفية؛ سيُحدّث السجل تلقائيًا"
            );
            await load();
        } catch (e) {
            const det = e?.response?.data?.detail;
            const msg = typeof det === "string"
                ? det
                : (det?.message || "فشل مزامنة الفترة المحددة");
            toast.error(msg);
            await load();
        } finally {
            setBusy(b => ({ ...b, syncOrders: false }));
        }
    };

    const handleSyncProducts = async () => {
        setBusy(b => ({ ...b, syncProducts: true }));
        try {
            const { data } = await api.post("/salla/sync/products");
            toast.success(`مزامنة المنتجات: ${data.created} جديد · ${data.updated} محدّث`);
            await load();
        } catch (e) {
            const det = e?.response?.data?.detail;
            const msg = typeof det === "string" ? det : (det?.message || "فشل مزامنة المنتجات");
            toast.error(msg);
            await load();
        } finally {
            setBusy(b => ({ ...b, syncProducts: false }));
        }
    };

    const handleReset = async () => {
        setShowReset(false);
        setBusy(b => ({ ...b, reset: true }));
        try {
            const { data } = await api.post("/salla/reset");
            setLiveStoreInfo(null);
            setCfgInputs({ client_id: "", client_secret: "", redirect_uri: "" });
            const parts = [];
            if (data.tokens_removed) parts.push("الـ tokens");
            if (data.config_removed) parts.push("بيانات OAuth");
            if (data.pending_states_removed) parts.push(`${data.pending_states_removed} محاولات معلّقة`);
            toast.success(parts.length
                ? `تم إلغاء الربط بنجاح وحذف: ${parts.join("، ")}. Make.com و Excel و PDF لم تتأثر.`
                : "لا توجد محاولات ربط قائمة — كل شيء نظيف.");
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الإلغاء");
        } finally {
            setBusy(b => ({ ...b, reset: false }));
        }
    };

    const connected = status?.connected;
    const configured = status?.configured;
    const cartsReadGranted = String(status?.scope || "")
        .replaceAll(",", " ")
        .split(/\s+/)
        .includes("carts.read");
    const cartEvents = (webhookMonitor?.events || [])
        .filter(event => event.group === "abandoned_carts");

    return (
        <div className="p-4 sm:p-6 space-y-6" data-testid="salla-integration-page" style={{ fontFamily: "Tajawal" }}>
            {/* Header */}
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-3">
                    <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white shadow-lg">
                        <Storefront size={28} weight="bold" />
                    </div>
                    <div>
                        <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2 flex-wrap">
                            ربط متجر سلة
                            {!loading && <StatusPill status={status?.status} configured={configured} />}
                        </h1>
                        <p className="text-sm text-slate-500 mt-1">
                            اتصال مباشر لقراءة المتجر والطلبات والمنتجات والسلات المتروكة ومراقبة Webhooks.
                        </p>
                    </div>
                </div>
            </div>

            {/* Make/PDF/Excel safety banner */}
            <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3 text-xs text-emerald-900 flex items-start gap-2" data-testid="salla-coexist-banner">
                <ShieldCheck size={16} weight="bold" className="flex-shrink-0 mt-0.5" />
                <div>
                    <span className="font-bold">Make.com ورفع PDF ورفع Excel يعملون كما هم</span> — الربط المباشر يعمل بالتوازي، والسلات تُحفظ للتحليل فقط دون اسم العميل أو بريده أو جواله أو عنوانه.
                </div>
            </div>

            {/* Not configured (no Client ID / Secret) — show inline form */}
            {!loading && !configured && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-5" data-testid="salla-not-configured">
                    <div className="flex items-start gap-3 mb-3">
                        <Lock size={22} weight="bold" className="text-amber-700 flex-shrink-0 mt-0.5" />
                        <div className="flex-1">
                            <h3 className="font-extrabold text-amber-900 text-lg mb-1">إعداد تطبيق سلة Partners</h3>
                            <p className="text-sm text-amber-900 leading-relaxed">
                                لتفعيل الربط، أنشئ تطبيقاً في{" "}
                                <a href="https://salla.partners" target="_blank" rel="noreferrer" className="font-bold underline inline-flex items-center gap-0.5">
                                    Salla Partners <ArrowSquareOut size={11} weight="bold" />
                                </a>{" "}
                                ثم انسخ القيم التالية في تطبيقك:
                            </p>
                            <p className="text-[11px] text-amber-800 mt-2 mb-1">Webhook URL (سجّله في Salla Partners Portal → Webhooks / App Events):</p>
                            <pre className="text-[11px] bg-slate-900 text-cyan-300 rounded-lg p-3 font-mono overflow-x-auto" data-testid="salla-webhook-url-snippet">
{`${window.location.origin}/api/salla/webhooks/app`}
                            </pre>
                        </div>
                    </div>

                    {/* Inline credentials form */}
                    <div className="bg-white rounded-lg border border-amber-200 p-4 space-y-3" data-testid="salla-config-form">
                        <h4 className="font-extrabold text-slate-900 text-sm mb-1">بيانات OAuth الخاصة بتطبيقك</h4>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1">Client ID</label>
                            <input
                                type="text"
                                value={cfgInputs.client_id}
                                onChange={(e) => setCfgInputs({ ...cfgInputs, client_id: e.target.value })}
                                placeholder="مثال: 1234567890abcdef"
                                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:border-indigo-500 font-mono"
                                data-testid="salla-input-client-id"
                            />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-slate-700 mb-1">
                                Client Secret {config?.has_client_secret && <span className="text-emerald-600 font-normal">(محفوظ بالفعل — اتركه فارغاً للإبقاء على القيمة الحالية)</span>}
                            </label>
                            <input
                                type="password"
                                value={cfgInputs.client_secret}
                                onChange={(e) => setCfgInputs({ ...cfgInputs, client_secret: e.target.value })}
                                placeholder={config?.has_client_secret ? "•••••••• (محفوظ)" : "أدخل Client Secret"}
                                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:border-indigo-500 font-mono"
                                data-testid="salla-input-client-secret"
                                autoComplete="off"
                            />
                        </div>
                        <div style={{ display: "none" }} aria-hidden="true">
                            {/* rev23 — Redirect URI hidden in Easy Mode. Kept in DOM tree
                                only to preserve DB/migration compatibility. Value is
                                neither used at connect time nor visible to the operator. */}
                            <input
                                type="hidden"
                                value={cfgInputs.redirect_uri}
                                onChange={(e) => setCfgInputs({ ...cfgInputs, redirect_uri: e.target.value })}
                                data-testid="salla-input-redirect-uri"
                            />
                        </div>
                        <button
                            type="button"
                            onClick={handleSaveConfig}
                            disabled={busy.saveConfig || !cfgInputs.client_id.trim()}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold text-sm"
                            data-testid="salla-save-config-btn"
                        >
                            <CheckCircle size={14} weight="bold" />
                            {busy.saveConfig ? "جاري الحفظ…" : "حفظ بيانات التطبيق"}
                        </button>
                    </div>
                </div>
            )}

            {/* Inline edit credentials (when configured + connected to allow updates) */}
            {!loading && configured && showConfigForm && (
                <div className="bg-slate-50 border border-slate-300 rounded-xl p-4 space-y-3" data-testid="salla-edit-config">
                    <h4 className="font-extrabold text-slate-900 text-sm">تعديل بيانات تطبيق سلة</h4>
                    <input
                        type="text"
                        value={cfgInputs.client_id}
                        onChange={(e) => setCfgInputs({ ...cfgInputs, client_id: e.target.value })}
                        placeholder="Client ID"
                        className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg font-mono"
                        data-testid="salla-edit-client-id"
                    />
                    <input
                        type="password"
                        value={cfgInputs.client_secret}
                        onChange={(e) => setCfgInputs({ ...cfgInputs, client_secret: e.target.value })}
                        placeholder={config?.has_client_secret ? "•••••••• (محفوظ — اتركه فارغاً)" : "Client Secret"}
                        className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg font-mono"
                        data-testid="salla-edit-client-secret"
                        autoComplete="off"
                    />
                    <div className="flex gap-2">
                        <button
                            type="button"
                            onClick={handleSaveConfig}
                            disabled={busy.saveConfig}
                            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm disabled:opacity-50"
                            data-testid="salla-save-config-btn-edit"
                        >
                            {busy.saveConfig ? "حفظ…" : "حفظ"}
                        </button>
                        <button
                            type="button"
                            onClick={() => setShowConfigForm(false)}
                            className="px-4 py-2 rounded-lg bg-slate-200 text-slate-700 font-bold text-sm"
                        >
                            إلغاء
                        </button>
                    </div>
                </div>
            )}

            {/* Connect card (configured but not connected, OR needs_reauth) */}
            {!loading && configured && (!connected || status?.status === "needs_reauth") && (
                <div className="bg-white border-2 border-dashed border-indigo-300 rounded-xl p-6 text-center" data-testid="salla-connect-card">
                    <div className="w-16 h-16 rounded-full bg-indigo-100 flex items-center justify-center mx-auto mb-4">
                        <Storefront size={32} weight="bold" className="text-indigo-600" />
                    </div>
                    <h3 className="font-extrabold text-xl text-slate-900 mb-1">
                        {status?.status === "needs_reauth" ? "انتهت صلاحية الربط" : "اربط متجر سلة الآن"}
                    </h3>
                    <p className="text-sm text-slate-500 mb-4 max-w-md mx-auto leading-relaxed">
                        سيتم فتح صفحة تثبيت التطبيق في سلة (Easy Mode). بعد الضغط على «تثبيت» ستُرسل سلة بيانات الربط تلقائياً إلى ميزان — دون الحاجة إلى صفحة موافقة إضافية.
                    </p>
                    <button
                        type="button"
                        onClick={handleConnect}
                        disabled={busy.connect}
                        className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold text-base shadow-lg shadow-indigo-200"
                        data-testid="salla-connect-btn"
                    >
                        <PlugsConnected size={18} weight="bold" />
                        {busy.connect ? "جاري التحويل…" : "ربط متجر سلة"}
                    </button>
                    {status?.last_error && (
                        <div className="mt-4 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-2 max-w-md mx-auto" data-testid="salla-last-error">
                            آخر خطأ: {status.last_error}
                        </div>
                    )}
                </div>
            )}

            {/* Connected state — store info + actions */}
            {!loading && connected && (
                <div className="bg-white border border-slate-200 rounded-xl overflow-hidden" data-testid="salla-connected-card">
                    <div className="bg-gradient-to-r from-emerald-50 to-emerald-100/50 border-b border-emerald-200 px-5 py-3 flex items-center gap-2">
                        <CheckCircle size={20} weight="fill" className="text-emerald-600" />
                        <div className="flex-1">
                            <div className="font-extrabold text-emerald-900">متصل بمتجر سلة</div>
                            <div className="text-[11px] text-emerald-700">
                                {status?.last_refreshed_at && (
                                    <>آخر تحديث للتوكن: {new Date(status.last_refreshed_at).toLocaleString("en-US")}</>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="p-5 space-y-1">
                        {status?.automatic_refresh_ready && (
                            <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-bold text-emerald-800" data-testid="salla-auto-refresh-ready">
                                التجديد التلقائي جاهز — يُجدَّد التوكن قبل انتهاء صلاحيته بيوم دون إيقاف ميزان.
                            </div>
                        )}
                        <InfoRow icon={Storefront} label="اسم المتجر" value={liveStoreInfo?.name || status?.store_name} testid="salla-info-name" />
                        <InfoRow icon={Globe} label="النطاق" value={liveStoreInfo?.domain || status?.store_domain} testid="salla-info-domain" mono />
                        <InfoRow icon={Hash} label="رقم المتجر" value={liveStoreInfo?.id || status?.store_id} testid="salla-info-id" mono />
                        <InfoRow icon={EnvelopeSimple} label="البريد" value={liveStoreInfo?.email || ""} testid="salla-info-email" mono />
                        <InfoRow icon={ShieldCheck} label="الباقة" value={liveStoreInfo?.plan || status?.store_plan} testid="salla-info-plan" />
                        <InfoRow icon={Info} label="حالة المتجر" value={liveStoreInfo?.status || status?.store_status} testid="salla-info-status" />
                        <InfoRow icon={Clock} label="تنتهي التوكن" value={status?.expires_at ? new Date(status.expires_at).toLocaleString("en-US") : ""} testid="salla-info-expires" />
                        <InfoRow icon={Lock} label="Scopes" value={status?.scope} testid="salla-info-scope" mono />
                    </div>
                    <div className="border-t border-slate-100 p-4 flex flex-wrap gap-2 bg-slate-50">
                        <button
                            type="button"
                            onClick={handleTest}
                            disabled={busy.test}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold text-sm"
                            data-testid="salla-test-btn"
                        >
                            <CheckCircle size={14} weight="bold" />
                            {busy.test ? "جاري الاختبار…" : "اختبار الاتصال"}
                        </button>
                        <button
                            type="button"
                            onClick={handleRefresh}
                            disabled={busy.refresh}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-200 hover:bg-slate-300 disabled:opacity-50 text-slate-800 font-bold text-sm"
                            data-testid="salla-refresh-btn"
                        >
                            <ArrowsClockwise size={14} weight="bold" className={busy.refresh ? "animate-spin" : ""} />
                            {busy.refresh ? "جاري الجلب…" : "جلب بيانات المتجر"}
                        </button>
                        <button
                            type="button"
                            onClick={handleConnect}
                            disabled={busy.connect}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-amber-100 hover:bg-amber-200 disabled:opacity-50 text-amber-800 font-bold text-sm border border-amber-300"
                            data-testid="salla-reconnect-btn"
                            title="ابدأ OAuth flow جديدة (مفيد بعد تغيير الـ scopes)"
                        >
                            <PlugsConnected size={14} weight="bold" />
                            إعادة الربط
                        </button>
                        <button
                            type="button"
                            onClick={() => setShowConfigForm(true)}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-sm border border-slate-200"
                            data-testid="salla-edit-config-btn"
                            title="تعديل Client ID / Secret"
                        >
                            <PencilSimple size={14} weight="bold" />
                            تعديل بيانات التطبيق
                        </button>
                        <button
                            type="button"
                            onClick={() => setShowDisconnect(true)}
                            disabled={busy.disconnect}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-rose-50 hover:bg-rose-100 disabled:opacity-50 text-rose-700 font-bold text-sm border border-rose-200 ms-auto"
                            data-testid="salla-disconnect-btn"
                        >
                            <LinkBreak size={14} weight="bold" />
                            إلغاء الربط
                        </button>
                    </div>
                </div>
            )}

            {/* Abandoned carts — historical import + live webhook coverage */}
            {!loading && connected && (
                <div className="bg-white border border-slate-200 rounded-xl overflow-hidden" data-testid="salla-abandoned-carts-card">
                    <div className="bg-gradient-to-r from-amber-50 to-orange-50 border-b border-amber-200 px-5 py-3 flex items-start gap-2">
                        <Storefront size={19} weight="bold" className="text-amber-700 mt-0.5" />
                        <div className="flex-1">
                            <h3 className="font-extrabold text-slate-900">ذاكرة السلات والعملاء V2</h3>
                            <p className="text-xs text-slate-600 mt-1 leading-relaxed">
                                استيراد تاريخي ومتابعة مباشرة للأحداث الأربعة. بيانات العميل وروابط الاستعادة تُحفظ مشفّرة، ولا تُخزَّن كنص صريح.
                            </p>
                        </div>
                        <span className="px-2 py-1 rounded-full bg-emerald-100 text-emerald-700 text-[10px] font-extrabold whitespace-nowrap">
                            قراءة آمنة
                        </span>
                    </div>

                    <div className="p-5 space-y-4">
                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                            {[
                                ["إجمالي السلات", cartStatus?.total_carts || 0, "text-slate-900"],
                                ["ما زالت متروكة", cartStatus?.active_carts || 0, "text-amber-700"],
                                ["تحولت إلى طلب", cartStatus?.purchased_carts || 0, "text-emerald-700"],
                                ["أحداث محفوظة", cartStatus?.webhook_events || 0, "text-indigo-700"],
                            ].map(([label, value, color]) => (
                                <div key={label} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                    <div className="text-[11px] font-bold text-slate-500">{label}</div>
                                    <div className={`text-2xl font-extrabold mt-1 font-mono ${color}`}>{Number(value).toLocaleString("en-US")}</div>
                                </div>
                            ))}
                        </div>

                        <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-3" data-testid="salla-cart-v2-memory-coverage">
                            <div className="flex items-center justify-between gap-2 mb-3">
                                <div>
                                    <div className="text-xs font-extrabold text-indigo-950">تغطية ذاكرة ميزان</div>
                                    <div className="text-[10px] text-indigo-700 mt-0.5">المخطط V{cartStatus?.schema_version || 1} · لا تعرض هذه الصفحة أي بيانات شخصية</div>
                                </div>
                                <span className="px-2 py-1 rounded-full bg-white border border-indigo-200 text-[10px] font-extrabold text-indigo-700 whitespace-nowrap">
                                    مشفّر عند الحفظ
                                </span>
                            </div>
                            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
                                {[
                                    ["هويات العملاء", cartStatus?.customer_identities || 0],
                                    ["مرتبطة بعميل", cartStatus?.identity_linked_carts || 0],
                                    ["مرتبطة بمصدر/حملة", cartStatus?.attributed_carts || 0],
                                    ["مرتبطة بطلب", cartStatus?.order_linked_carts || 0],
                                    ["طلبات في ملف العميل", cartStatus?.customer_memory_orders || 0],
                                    ["روابط/كوبونات مشفرة", cartStatus?.encrypted_private_carts || 0],
                                ].map(([label, value]) => (
                                    <div key={label} className="rounded-lg bg-white border border-indigo-100 px-2.5 py-2">
                                        <div className="text-[10px] font-bold text-slate-500 leading-tight">{label}</div>
                                        <div className="text-lg font-extrabold text-indigo-800 font-mono mt-1">{Number(value).toLocaleString("en-US")}</div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 flex items-center gap-3">
                            <CheckCircle size={22} weight="fill" className="text-emerald-600 flex-shrink-0" />
                            <div>
                                <div className="font-extrabold text-emerald-950 text-sm">
                                    السلات الجديدة وتحديثاتها تعمل مباشرة
                                </div>
                                <div className="text-xs text-emerald-800 mt-1" data-testid="salla-cart-live-webhooks-mode">
                                    تم إيقاف الاستيراد التاريخي حسب الإعداد المعتمد. تصل السلات الجديدة وتحديث الحالة والتحويل إلى طلب عبر Webhook فقط.
                                </div>
                            </div>
                        </div>

                        <div>
                            <div className="flex items-center justify-between gap-2 mb-2">
                                <h4 className="font-extrabold text-sm text-slate-800">حالة أحداث السلات الأربعة</h4>
                                <span className="text-[11px] text-slate-500">
                                    وصل {cartEvents.filter(event => event.observed).length} من {cartEvents.length || 4}
                                </span>
                            </div>
                            <div className="grid sm:grid-cols-2 gap-2" data-testid="salla-cart-webhook-events">
                                {cartEvents.map(event => (
                                    <div key={event.event} className="rounded-lg border border-slate-200 px-3 py-2 flex items-center gap-2">
                                        {event.observed
                                            ? <CheckCircle size={16} weight="fill" className="text-emerald-600 flex-shrink-0" />
                                            : <Clock size={16} weight="bold" className="text-slate-400 flex-shrink-0" />}
                                        <div className="min-w-0 flex-1">
                                            <div className="text-xs font-extrabold text-slate-800">{event.label}</div>
                                            <div className="text-[10px] text-slate-500 font-mono truncate">{event.event}</div>
                                        </div>
                                        <div className="text-left flex-shrink-0">
                                            <div className={`text-[10px] font-extrabold ${event.observed ? "text-emerald-700" : "text-slate-500"}`}>
                                                {event.observed ? `وصل ${event.delivery_count || 0}` : "لم يصل بعد"}
                                            </div>
                                            {event.last_received_at && (
                                                <div className="text-[9px] text-slate-400" dir="ltr">
                                                    {new Date(event.last_received_at).toLocaleString("en-US")}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                ))}
                                {cartEvents.length === 0 && (
                                    <div className="sm:col-span-2 text-center py-4 text-xs text-slate-400">
                                        جاري تحميل سجل Webhooks…
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Sync section + logs — shown whenever connected */}
            {!loading && connected && (
                <div className="bg-white border border-slate-200 rounded-xl overflow-hidden" data-testid="salla-sync-card">
                    <div className="bg-gradient-to-r from-indigo-50 to-violet-50 border-b border-slate-200 px-5 py-3">
                        <h3 className="font-extrabold text-slate-900 flex items-center gap-2">
                            <ArrowsClockwise size={18} weight="bold" className="text-indigo-600" />
                            مزامنة البيانات من سلة (Salla Direct)
                        </h3>
                        <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                            مصدر جديد بجانب Make.com و Excel — يتم حفظه باسم <code className="font-mono bg-slate-100 px-1 rounded">salla_direct</code>. لا يُلغي أي مصدر سابق، ولا يستبدل بيانات Make.com لحماية البيانات الفورية.
                        </p>
                    </div>
                    <div className="p-5 space-y-3">
                        <div
                            className="flex flex-wrap items-end gap-3 rounded-xl border border-indigo-200 bg-indigo-50 p-3"
                            data-testid="salla-date-range-sync"
                        >
                            <div>
                                <label className="block text-xs font-bold text-slate-600 mb-1">
                                    من تاريخ
                                </label>
                                <input
                                    type="date"
                                    value="2026-07-01"
                                    disabled
                                    className="px-3 py-2 rounded-lg border border-slate-300 bg-slate-100 text-sm font-mono"
                                />
                            </div>

                            <div>
                                <label className="block text-xs font-bold text-slate-600 mb-1">
                                    إلى تاريخ
                                </label>
                                <input
                                    type="date"
                                    value={syncToDate}
                                    min="2026-07-01"
                                    onChange={(e) => setSyncToDate(e.target.value)}
                                    className="px-3 py-2 rounded-lg border border-slate-300 bg-white text-sm font-mono"
                                    data-testid="salla-sync-to-date"
                                />
                            </div>

                            <button
                                type="button"
                                onClick={handleSyncOrdersRange}
                                disabled={busy.syncOrders || hasRunningOrderSync || !syncToDate}
                                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-bold text-sm"
                                data-testid="salla-sync-date-range-btn"
                            >
                                <ArrowsClockwise
                                    size={14}
                                    weight="bold"
                                    className={busy.syncOrders || hasRunningOrderSync ? "animate-spin" : ""}
                                />
                                {busy.syncOrders || hasRunningOrderSync
                                    ? "جاري مزامنة الفترة…"
                                    : "مزامنة من 01-07 إلى التاريخ المحدد"}
                            </button>
                        </div>

                        <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            onClick={handleSyncOrders}
                            disabled={busy.syncOrders || hasRunningOrderSync}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold text-sm"
                            data-testid="salla-sync-orders-btn"
                        >
                            <ArrowsClockwise size={14} weight="bold" className={busy.syncOrders || hasRunningOrderSync ? "animate-spin" : ""} />
                            {busy.syncOrders || hasRunningOrderSync ? "جاري المزامنة…" : "مزامنة الطلبات الآن (آخر 30 يوماً)"}
                        </button>
                        <button
                            type="button"
                            onClick={handleSyncProducts}
                            disabled={busy.syncProducts}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold text-sm"
                            data-testid="salla-sync-products-btn"
                        >
                            <Package size={14} weight="bold" className={busy.syncProducts ? "animate-spin" : ""} />
                            {busy.syncProducts ? "جاري المزامنة…" : "مزامنة المنتجات"}
                        </button>
                        <button
                            type="button"
                            onClick={() => navigate("/salla-sources")}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-sm border border-slate-300 ms-auto"
                            data-testid="salla-compare-sources-btn"
                        >
                            <ChartPieSlice size={14} weight="bold" />
                            مقارنة المصادر (Excel / Make / Salla Direct)
                        </button>
                        </div>
                    </div>

                    {/* Sync logs */}
                    <div className="border-t border-slate-100 bg-slate-50">
                        <div className="px-5 py-3 flex items-center justify-between">
                            <h4 className="font-bold text-sm text-slate-700">سجل عمليات المزامنة</h4>
                            <span className="text-[11px] text-slate-500">{syncLogs.length} عملية</span>
                        </div>
                        {syncLogs.length === 0 ? (
                            <div className="text-center py-6 text-xs text-slate-400" data-testid="salla-sync-empty">
                                لا توجد عمليات مزامنة بعد. اضغط "مزامنة الطلبات الآن" للبدء.
                            </div>
                        ) : (
                            <div className="max-h-96 overflow-y-auto">
                                <table className="mezan-table compact w-full text-xs" data-testid="salla-sync-logs">
                                    <thead className="bg-slate-100 text-slate-600 text-[11px] sticky top-0">
                                        <tr>
                                            <th className="text-right px-3 py-2">النوع</th>
                                            <th className="text-right px-3 py-2">الحالة</th>
                                            <th className="text-right px-3 py-2">جديد</th>
                                            <th className="text-right px-3 py-2">محدّث</th>
                                            <th className="text-right px-3 py-2">أخطاء</th>
                                            <th className="text-right px-3 py-2">صفحات</th>
                                            <th className="text-right px-3 py-2">بدأت</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-200">
                                        {syncLogs.map((log) => (
                                            <tr key={log.id} className="hover:bg-white">
                                                <td className="px-3 py-2 font-bold">{log.kind === "orders" ? "طلبات" : log.kind === "products" ? "منتجات" : log.kind === "abandoned_carts" ? "سلات متروكة" : log.kind}</td>
                                                <td className="px-3 py-2">
                                                    {log.status === "completed" && <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 text-[10px] font-bold">✓ مكتمل</span>}
                                                    {log.status === "running" && <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-bold">⏳ جارٍ</span>}
                                                    {log.status === "failed" && <span className="px-1.5 py-0.5 rounded bg-rose-100 text-rose-700 text-[10px] font-bold" title={log.last_error || ""}>✗ فشل</span>}
                                                    {log.status === "interrupted" && <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 text-[10px] font-bold" title={log.last_error || ""}>■ متوقفة</span>}
                                                </td>
                                                <td className="px-3 py-2 text-emerald-700 font-bold">{log.created || 0}</td>
                                                <td className="px-3 py-2 text-sky-700 font-bold">{log.updated || 0}</td>
                                                <td className="px-3 py-2 text-rose-700 font-bold">{log.errors_count || 0}</td>
                                                <td className="px-3 py-2 text-slate-500">{log.pages_fetched || 0}</td>
                                                <td className="px-3 py-2 text-slate-500 text-[11px]" dir="ltr">
                                                    {log.started_at ? new Date(log.started_at).toLocaleString("en-US") : "—"}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Danger zone — full reset (always visible) */}
            {!loading && (config?.client_id || connected || syncLogs.length > 0) && (
                <div className="bg-rose-50 border border-rose-200 rounded-xl p-4" data-testid="salla-danger-zone">
                    <div className="flex items-start justify-between gap-3 flex-wrap">
                        <div className="flex items-start gap-2 flex-1 min-w-0">
                            <Warning size={18} weight="bold" className="text-rose-600 flex-shrink-0 mt-0.5" />
                            <div>
                                <h4 className="font-extrabold text-rose-900 text-sm">إلغاء جميع محاولات الربط</h4>
                                <p className="text-[12px] text-rose-800 mt-1 leading-relaxed">
                                    يحذف <span className="font-bold">بيانات OAuth + الـ tokens + المحاولات المعلّقة</span> دفعة واحدة لتبدأ من الصفر.
                                    لن يتأثر Make.com أو Excel أو PDF أو أي بيانات حسابية مخزّنة.
                                </p>
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={() => setShowReset(true)}
                            disabled={busy.reset}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white font-bold text-sm flex-shrink-0"
                            data-testid="salla-reset-btn"
                        >
                            <LinkBreak size={14} weight="bold" />
                            {busy.reset ? "جاري الإلغاء…" : "إعادة ضبط الربط"}
                        </button>
                    </div>
                </div>
            )}

            {loading && (
                <div className="text-center py-12 text-slate-500" data-testid="salla-loading">
                    <ArrowsClockwise size={28} className="animate-spin mx-auto mb-2" />
                    جاري التحميل…
                </div>
            )}

            <ConfirmModal
                open={showDisconnect}
                title="إلغاء الربط مع سلة"
                description={
                    "سيتم حذف Access Token و Refresh Token المحفوظة محلياً.\n\n" +
                    "ملاحظة: لن يتأثر Make.com ولا رفع PDF/Excel — يمكنك إعادة الربط لاحقاً عند الحاجة."
                }
                confirmLabel="نعم، الغِ الربط"
                onConfirm={handleDisconnect}
                onCancel={() => setShowDisconnect(false)}
                danger
            />

            <ConfirmModal
                open={showReset}
                title="إعادة ضبط الربط بالكامل"
                description={
                    "سيتم حذف:\n" +
                    "• بيانات OAuth (Client ID و Client Secret)\n" +
                    "• Access Token و Refresh Token\n" +
                    "• جميع محاولات OAuth المعلّقة\n\n" +
                    "Make.com و Excel و PDF لن تتأثر. الحسابات والطلبات المخزّنة لن تُمسح."
                }
                confirmLabel="نعم، أعد الضبط"
                onConfirm={handleReset}
                onCancel={() => setShowReset(false)}
                danger
            />
        </div>
    );
}
