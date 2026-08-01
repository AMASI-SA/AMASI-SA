import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
    ChartLineUp,
    CheckCircle,
    Clock,
    Database,
    Gear,
    WarningCircle,
} from "@phosphor-icons/react";

import { getIntegrationsOverview } from "../services/integrationsV2";

const CONNECTION_LABELS = {
    connected: "متصل",
    data_available: "بيانات متاحة",
    needs_reauth: "يحتاج إعادة ربط",
    not_connected: "غير متصل",
    not_configured: "غير مهيأ",
    expired: "انتهت الصلاحية",
    error: "خطأ",
    planned: "مستقبلي",
    unknown: "غير محسوم",
};

const PROVENANCE_LABELS = {
    api_connection: "ربط API مباشر",
    legacy_integration: "تكامل قائم سابقًا",
    data_feed: "تغذية بيانات فقط",
    disconnected: "غير مرتبط",
    planned: "مستقبلي",
    unknown: "مصدر غير محسوم",
};

export function isAllPlatformsLocation(location = {}) {
    if (location.pathname !== "/ads-manager") return false;
    const provider = new URLSearchParams(location.search || "").get("provider");
    return !provider || provider === "all";
}

function displayDateTime(value) {
    if (!value) return "لم تبدأ مزامنة تقارير";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString("ar-SA", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function statusTone(status) {
    if (["connected", "data_available"].includes(status)) {
        return "border-emerald-200 bg-emerald-50 text-emerald-700";
    }
    if (["needs_reauth", "expired", "error"].includes(status)) {
        return "border-rose-200 bg-rose-50 text-rose-700";
    }
    return "border-amber-200 bg-amber-50 text-amber-700";
}

export function GoogleAdsReadinessCard({ integration, loading = false, error = "" }) {
    const status = integration?.connection_status || "unknown";
    const connected = ["connected", "data_available"].includes(status);
    const accountsCount = Array.isArray(integration?.accounts)
        ? integration.accounts.length
        : 0;
    const healthScore = integration?.health?.score;

    return (
        <article
            className="rounded-xl border border-blue-200 bg-white p-4 shadow-sm"
            data-testid="ads-provider-google"
        >
            <div className="flex items-start justify-between gap-3">
                <div>
                    <h3 className="font-black text-slate-950">إعلانات Google</h3>
                    <p className="mt-1 text-[11px] font-bold text-slate-500">
                        {PROVENANCE_LABELS[integration?.connection_provenance]
                            || "مصدر غير محسوم"}
                    </p>
                </div>
                <span className={`rounded-full border px-2.5 py-1 text-[11px] font-black ${statusTone(status)}`}>
                    {loading ? "جارٍ الفحص" : CONNECTION_LABELS[status] || "غير محسوم"}
                </span>
            </div>

            <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
                <div>
                    <dt className="font-bold text-slate-500">حالة التطبيق</dt>
                    <dd className="mt-1 flex items-center gap-1 font-black text-slate-800">
                        {connected
                            ? <CheckCircle size={16} weight="fill" className="text-emerald-600" />
                            : <WarningCircle size={16} weight="fill" className="text-amber-600" />}
                        {CONNECTION_LABELS[status] || "غير محسوم"}
                    </dd>
                </div>
                <div>
                    <dt className="font-bold text-slate-500">الحسابات المكتشفة</dt>
                    <dd className="mt-1 flex items-center gap-1 font-mono font-black text-slate-800">
                        <Database size={16} weight="duotone" />
                        {accountsCount.toLocaleString("en-US")}
                    </dd>
                </div>
                <div>
                    <dt className="font-bold text-slate-500">آخر حالة ربط</dt>
                    <dd className="mt-1 flex items-start gap-1 font-black leading-5 text-slate-800">
                        <Clock size={16} weight="duotone" className="mt-0.5 shrink-0" />
                        {displayDateTime(integration?.last_sync_at)}
                    </dd>
                </div>
                <div>
                    <dt className="font-bold text-slate-500">صحة الربط</dt>
                    <dd className="mt-1 font-mono font-black text-slate-800">
                        {healthScore === null || healthScore === undefined
                            ? "غير متاحة"
                            : `${Number(healthScore).toLocaleString("en-US")}%`}
                    </dd>
                </div>
            </dl>

            <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2.5 text-xs text-blue-800">
                <div className="flex items-center gap-2 font-black">
                    <ChartLineUp size={18} weight="duotone" />
                    تقارير الأداء غير مفعّلة بعد
                </div>
                <p className="mt-1.5 font-semibold leading-5">
                    يظهر Google Ads كمنصة رابعة، لكن الصرف والحملات والتحويلات وROAS
                    لا تدخل في الإجماليات حتى يتوفر مصدر تقارير Google أصلي وموثّق.
                </p>
            </div>

            {error && (
                <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] font-bold leading-5 text-rose-700">
                    تعذر تحديث حالة Google Ads الآن؛ لم تُستخدم أي أرقام بديلة.
                </p>
            )}

            <div className="mt-3 grid grid-cols-2 gap-2">
                <a
                    href="/ads-manager?provider=google"
                    className="inline-flex min-h-10 items-center justify-center gap-1 rounded-lg border border-blue-200 bg-blue-50 px-3 text-xs font-black text-blue-800 hover:bg-blue-100"
                >
                    <ChartLineUp size={16} weight="bold" />
                    صفحة Google Ads
                </a>
                <a
                    href="/integrations-v2?provider=google_ads"
                    className="inline-flex min-h-10 items-center justify-center gap-1 rounded-lg border border-slate-200 bg-slate-50 px-3 text-xs font-black text-slate-700 hover:bg-slate-100"
                >
                    <Gear size={16} weight="bold" />
                    إدارة التطبيق
                </a>
            </div>
        </article>
    );
}

export default function GoogleAdsAllPlatformsCard({ location }) {
    const visible = isAllPlatformsLocation(location);
    const [target, setTarget] = useState(null);
    const [integration, setIntegration] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        if (!visible) {
            setTarget(null);
            return undefined;
        }

        let frame = 0;
        const locate = () => {
            const nextTarget = document.querySelector(
                '[data-testid="ads-manager-page"] [aria-labelledby="ads-freshness-heading"] > .grid',
            );
            if (nextTarget) {
                nextTarget.classList.add("xl:grid-cols-4");
                setTarget((current) => current === nextTarget ? current : nextTarget);
                return;
            }
            frame = window.requestAnimationFrame(locate);
        };
        locate();

        const observer = new MutationObserver(() => {
            if (!document.body.contains(target)) locate();
        });
        observer.observe(document.body, { childList: true, subtree: true });

        return () => {
            if (frame) window.cancelAnimationFrame(frame);
            observer.disconnect();
            if (target) target.classList.remove("xl:grid-cols-4");
        };
    }, [visible, location?.pathname, location?.search, target]);

    useEffect(() => {
        if (!visible) return undefined;
        let active = true;
        let interval;

        const load = async () => {
            setLoading(true);
            setError("");
            try {
                const overview = await getIntegrationsOverview();
                const google = overview.providers.find(
                    (provider) => provider.provider === "google_ads",
                ) || null;
                if (active) setIntegration(google);
            } catch (requestError) {
                if (active) setError(requestError?.message || "google_ads_status_unavailable");
            } finally {
                if (active) setLoading(false);
            }
        };

        load();
        interval = window.setInterval(load, 60_000);
        return () => {
            active = false;
            if (interval) window.clearInterval(interval);
        };
    }, [visible]);

    if (!visible || !target) return null;
    return createPortal(
        <GoogleAdsReadinessCard
            integration={integration}
            loading={loading}
            error={error}
        />,
        target,
    );
}
