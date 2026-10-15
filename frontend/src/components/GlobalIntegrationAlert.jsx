import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowClockwise, WarningCircle } from "@phosphor-icons/react";

import { getIntegrationsOverview } from "../services/integrationsV2";
import { integrationNeedsGlobalAlert } from "./globalIntegrationAlertState";

function destination(provider) {
    return provider?.actions?.settings?.href
        || provider?.actions?.reconnect?.href
        || "/integrations-v2";
}

export default function GlobalIntegrationAlert() {
    const [providers, setProviders] = useState([]);
    const [loading, setLoading] = useState(false);

    const refresh = useCallback(async () => {
        setLoading(true);
        try {
            const overview = await getIntegrationsOverview();
            setProviders(
                (overview?.providers || []).filter(integrationNeedsGlobalAlert),
            );
        } catch (_) {
            // The overview is owner-only. A user without access should keep
            // the normal page rather than seeing a broken global header.
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        refresh();
        const timer = window.setInterval(refresh, 60_000);
        return () => window.clearInterval(timer);
    }, [refresh]);

    const title = useMemo(() => {
        if (providers.length === 1) {
            return `تنبيه: تطبيق ${providers[0].name_ar || providers[0].name} متوقف`;
        }
        return `تنبيه: ${providers.length} تطبيقات متوقفة أو تحتاج إعادة ربط`;
    }, [providers]);

    if (providers.length === 0) return null;

    return (
        <div
            className="border-b border-rose-300 bg-rose-50"
            dir="rtl"
            data-testid="global-integration-alert"
        >
            <div className="mx-auto flex max-w-[1900px] flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
                <WarningCircle
                    size={24}
                    weight="fill"
                    className="shrink-0 text-rose-600"
                />
                <div className="min-w-0 flex-1">
                    <div className="font-black text-rose-950">{title}</div>
                    <div className="mt-0.5 text-xs font-semibold text-rose-800">
                        توقف التشغيل الآلي لحماية البيانات. افتح إعدادات التطبيق لمراجعة الربط وآخر خطأ.
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {providers.map((provider) => (
                        <Link
                            key={provider.provider}
                            to={destination(provider)}
                            className="rounded-lg bg-rose-700 px-3 py-2 text-xs font-extrabold text-white hover:bg-rose-800"
                            data-testid={`global-integration-alert-link-${provider.provider}`}
                        >
                            مراجعة {provider.name_ar || provider.name}
                        </Link>
                    ))}
                    <button
                        type="button"
                        onClick={refresh}
                        disabled={loading}
                        className="inline-flex items-center gap-1 rounded-lg border border-rose-300 bg-white px-3 py-2 text-xs font-extrabold text-rose-800 disabled:opacity-50"
                        aria-label="تحديث حالة التطبيقات"
                    >
                        <ArrowClockwise
                            size={14}
                            className={loading ? "animate-spin" : ""}
                        />
                        تحديث
                    </button>
                </div>
            </div>
        </div>
    );
}
