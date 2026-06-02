import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
    Storefront, CheckCircle, Warning, ArrowsClockwise, LinkBreak,
    PlugsConnected, ShieldCheck, Info, ArrowSquareOut, Lock,
    Globe, EnvelopeSimple, Hash, Clock, Plus,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";

/**
 * /settings/salla — "ربط متجر سلة" (Phase 1)
 *
 * Phase 1 scope:
 *   - Show connection status
 *   - "ربط متجر سلة" button → starts OAuth flow at Salla
 *   - On callback success: show store info, last refresh, plan, status
 *   - "اختبار الاتصال" — live call to /store/info
 *   - "جلب بيانات المتجر" — re-fetch fresh
 *   - "إلغاء الربط" — local disconnect (no remote revoke yet)
 *
 * Phase 2 will add: webhook creation buttons + event log + monitoring.
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
    const [busy, setBusy] = useState({ connect: false, test: false, refresh: false, disconnect: false });
    const [showDisconnect, setShowDisconnect] = useState(false);
    const [liveStoreInfo, setLiveStoreInfo] = useState(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/salla/status");
            setStatus(data);
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

    const handleConnect = async () => {
        setBusy(b => ({ ...b, connect: true }));
        try {
            const { data } = await api.get("/salla/oauth/login");
            if (!data?.authorize_url) {
                throw new Error("No authorize_url returned");
            }
            // Full-page navigation — OAuth dance requires real browser redirect
            window.location.href = data.authorize_url;
        } catch (e) {
            const detail = e?.response?.data?.detail || e.message || "فشل بدء الربط";
            toast.error(typeof detail === "string" ? detail : detail.message || "فشل بدء الربط");
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

    const connected = status?.connected;
    const configured = status?.configured;

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
                            ربط مباشر بين سلة والنظام المحاسبي عبر OAuth — Phase 1: الاتصال وقراءة بيانات المتجر.
                        </p>
                    </div>
                </div>
            </div>

            {/* Phase notice + Make/PDF/Excel safety banner */}
            <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3 text-xs text-emerald-900 flex items-start gap-2" data-testid="salla-coexist-banner">
                <ShieldCheck size={16} weight="bold" className="flex-shrink-0 mt-0.5" />
                <div>
                    <span className="font-bold">Make.com و رفع PDF و رفع Excel يعملون كما هم</span> — لن يتم تعطيلهم أو إخفاؤهم. هذا الربط الجديد يعمل بالتوازي، ويبدأ بقراءة بيانات المتجر فقط في Phase 1.
                </div>
            </div>

            {/* Not configured (no Client ID / Secret in .env) */}
            {!loading && !configured && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-5" data-testid="salla-not-configured">
                    <div className="flex items-start gap-3">
                        <Lock size={22} weight="bold" className="text-amber-700 flex-shrink-0 mt-0.5" />
                        <div className="flex-1">
                            <h3 className="font-extrabold text-amber-900 text-lg mb-1">Client ID و Client Secret غير مُعدّين بعد</h3>
                            <p className="text-sm text-amber-900 leading-relaxed">
                                لتفعيل الربط، يجب أولاً إنشاء تطبيق في
                                {" "}
                                <a href="https://salla.partners" target="_blank" rel="noreferrer" className="font-bold underline inline-flex items-center gap-0.5">
                                    Salla Partners <ArrowSquareOut size={11} weight="bold" />
                                </a>
                                {" "}والحصول على <span className="font-bold">Client ID</span> و <span className="font-bold">Client Secret</span>، ثم إضافتهما في ملف <code className="bg-amber-100 px-1 rounded font-mono text-xs">backend/.env</code>:
                            </p>
                            <pre className="mt-2 text-[11px] bg-slate-900 text-emerald-300 rounded-lg p-3 font-mono overflow-x-auto" data-testid="salla-env-snippet">
{`SALLA_CLIENT_ID=...your-client-id-from-partners
SALLA_CLIENT_SECRET=...your-client-secret-from-partners`}
                            </pre>
                            <p className="text-[11px] text-amber-800 mt-2 leading-relaxed">
                                وأضف Redirect URI التالي في تطبيقك على Partners Portal:
                            </p>
                            <pre className="mt-1 text-[11px] bg-slate-900 text-cyan-300 rounded-lg p-3 font-mono overflow-x-auto" data-testid="salla-redirect-uri-snippet">
{`${window.location.origin}/api/salla/oauth/callback`}
                            </pre>
                        </div>
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
                        سيتم تحويلك إلى صفحة موافقة سلة الرسمية. بعد الموافقة، سنحصل تلقائياً على <span className="font-bold">Access Token</span> و <span className="font-bold">Refresh Token</span> و <span className="font-bold">Store ID</span>، وسنحفظها مشفّرة في قاعدة البيانات.
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
                                    <>آخر تحديث للتوكن: {new Date(status.last_refreshed_at).toLocaleString("ar-SA")}</>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="p-5 space-y-1">
                        <InfoRow icon={Storefront} label="اسم المتجر" value={liveStoreInfo?.name || status?.store_name} testid="salla-info-name" />
                        <InfoRow icon={Globe} label="النطاق" value={liveStoreInfo?.domain || status?.store_domain} testid="salla-info-domain" mono />
                        <InfoRow icon={Hash} label="رقم المتجر" value={liveStoreInfo?.id || status?.store_id} testid="salla-info-id" mono />
                        <InfoRow icon={EnvelopeSimple} label="البريد" value={liveStoreInfo?.email || ""} testid="salla-info-email" mono />
                        <InfoRow icon={ShieldCheck} label="الباقة" value={liveStoreInfo?.plan || status?.store_plan} testid="salla-info-plan" />
                        <InfoRow icon={Info} label="حالة المتجر" value={liveStoreInfo?.status || status?.store_status} testid="salla-info-status" />
                        <InfoRow icon={Clock} label="تنتهي التوكن" value={status?.expires_at ? new Date(status.expires_at).toLocaleString("ar-SA") : ""} testid="salla-info-expires" />
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

            {/* Phase 2/3/4 preview */}
            {!loading && connected && (
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-4" data-testid="salla-roadmap">
                    <h4 className="font-extrabold text-slate-900 text-sm mb-2 flex items-center gap-1">
                        <Plus size={14} weight="bold" className="text-slate-500" /> قريباً (مراحل لاحقة)
                    </h4>
                    <ul className="text-xs text-slate-600 space-y-1 leading-relaxed list-disc list-inside">
                        <li>Phase 2: إنشاء Webhooks تلقائياً + استقبال أحداث (إنشاء/تعديل/إلغاء/استرجاع) + سجل أحداث.</li>
                        <li>Phase 3: مزامنة الطلبات القديمة + ربطها بحسابات الأرباح.</li>
                        <li>Phase 4: شاشة مراقبة الربط + أداة مقارنة Salla مقابل النظام.</li>
                    </ul>
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
        </div>
    );
}
