import {
    ArrowClockwise,
    CaretDown,
    CheckCircle,
    Gear,
    LinkSimple,
    WarningCircle,
} from "@phosphor-icons/react";
import ProviderMark from "./ProviderMark";
import SnapchatAccountScope from "./SnapchatAccountScope";
import MetaReportingControl from "./MetaReportingControl";
import TikTokReportingSyncControl from "./TikTokReportingSyncControl";

const STATUS = {
    connected: ["متصل", "border-emerald-200 bg-emerald-50 text-emerald-700"],
    data_available: ["بيانات فقط", "border-sky-200 bg-sky-50 text-sky-700"],
    not_connected: ["غير متصل", "border-slate-200 bg-slate-50 text-slate-600"],
    not_configured: ["غير مُعد", "border-slate-200 bg-slate-50 text-slate-600"],
    needs_reauth: ["يحتاج إعادة ربط", "border-amber-200 bg-amber-50 text-amber-700"],
    expired: ["انتهت الصلاحية", "border-rose-200 bg-rose-50 text-rose-700"],
    error: ["خطأ", "border-rose-200 bg-rose-50 text-rose-700"],
    planned: ["مستقبلاً", "border-violet-200 bg-violet-50 text-violet-700"],
    unknown: ["غير معروف", "border-slate-200 bg-slate-50 text-slate-600"],
};

const PROVENANCE = {
    api_connection: ["ربط API مباشر", "border-emerald-200 bg-emerald-50 text-emerald-700"],
    legacy_integration: ["تكامل قائم سابقًا", "border-amber-200 bg-amber-50 text-amber-800"],
    data_feed: ["تغذية بيانات فقط", "border-sky-200 bg-sky-50 text-sky-700"],
    disconnected: ["غير مرتبط", "border-slate-200 bg-slate-50 text-slate-600"],
    planned: ["مستقبلي", "border-violet-200 bg-violet-50 text-violet-700"],
    unknown: ["غير معروف", "border-slate-200 bg-slate-50 text-slate-600"],
};

const DATA_QUALITY = {
    complete: "مكتملة",
    good: "جيدة",
    healthy: "جيدة",
    partial: "جزئية",
    stale: "متأخرة",
    degraded: "تحتاج مراجعة",
    missing: "لا توجد بيانات",
    unavailable: "غير متاحة",
    unknown: "غير معروفة",
    planned: "مستقبلاً",
};

function formatDate(value) {
    if (!value) return "لم تتم مزامنة";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "غير معروف";
    return new Intl.DateTimeFormat("ar-SA", {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}

function accountId(account = {}) {
    return account.external_account_id
        || account.ad_account_id
        || account.store_id
        || account.mezan_integration_account_id
        || "";
}

function ActionButton({ label, Icon, enabled, onClick, primary, testid }) {
    if (!enabled) return null;
    return (
        <button
            type="button"
            onClick={onClick}
            className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border px-3 text-xs font-extrabold transition ${
                primary
                    ? "border-emerald-800 bg-emerald-800 text-white hover:bg-emerald-900"
                    : "border-slate-200 bg-white text-slate-700 hover:border-emerald-300 hover:text-emerald-800"
            }`}
            data-testid={testid}
        >
            <Icon size={16} weight="bold" />
            {label}
        </button>
    );
}

function ProviderTools({ integration, snapchatScope }) {
    if (integration.provider === "snapchat_ads") {
        return (
            <SnapchatAccountScope
                accounts={integration.accounts}
                initialSelection={snapchatScope?.selection || null}
                initialSummary={snapchatScope?.summary || null}
            />
        );
    }
    if (integration.provider === "meta_ads") {
        return <MetaReportingControl integration={integration} />;
    }
    if (integration.provider === "tiktok_ads") {
        return <TikTokReportingSyncControl integration={integration} />;
    }
    return (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
            لا توجد أدوات اختيار إضافية لهذه المنصة حاليًا. الحسابات الظاهرة أعلاه هي النطاق المتاح للقراءة.
        </div>
    );
}

export default function AdvertisingAccountCard({
    integration,
    testing = false,
    syncing = false,
    settingsAvailable = false,
    snapchatScope = null,
    onTest,
    onSync,
    onSettings,
}) {
    const accounts = Array.isArray(integration.accounts) ? integration.accounts : [];
    const [statusLabel, statusClass] = STATUS[integration.connection_status] || STATUS.unknown;
    const [provenanceLabel, provenanceClass] = (
        PROVENANCE[integration.connection_provenance] || PROVENANCE.unknown
    );
    const canTest = Boolean(integration.actions?.test_connection?.enabled) && !testing;
    const canSync = integration.provider === "snapchat_ads"
        && Boolean(integration.actions?.sync_data?.enabled)
        && !syncing;
    const canRelink = settingsAvailable && Boolean(integration.actions?.reconnect?.enabled);
    const canOpenSettings = settingsAvailable && Boolean(integration.actions?.settings?.enabled);
    const currentPermissions = integration.permissions?.current || [];
    const missingPermissions = integration.permissions?.missing || [];
    const hasProviderTools = ["snapchat_ads", "meta_ads", "tiktok_ads", "google_ads"].includes(
        integration.provider,
    );
    const hasActions = canTest || canSync || canRelink || canOpenSettings;

    return (
        <article
            className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"
            data-testid={`integration-card-${integration.provider}`}
            data-layout="advertising-account-compact"
        >
            <header className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                    <ProviderMark provider={integration.provider} />
                    <div className="min-w-0">
                        <h2 className="truncate text-lg font-extrabold text-slate-950">
                            {integration.name_ar}
                        </h2>
                        <div className="truncate text-xs font-semibold text-slate-400">
                            {integration.name}
                        </div>
                    </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                    <span className={`rounded-full border px-2.5 py-1 text-[11px] font-extrabold ${provenanceClass}`}>
                        {provenanceLabel}
                    </span>
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${statusClass}`}>
                        {statusLabel}
                    </span>
                </div>
            </header>

            <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-4" data-testid={`advertising-account-summary-${integration.provider}`}>
                <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <div className="text-[10px] font-bold text-slate-400">الحسابات الظاهرة</div>
                    <div className="mt-1 font-mono text-xl font-black text-slate-900">{accounts.length}</div>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <div className="text-[10px] font-bold text-slate-400">حالة الاتصال</div>
                    <div className="mt-1 text-sm font-extrabold text-slate-800">{statusLabel}</div>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <div className="text-[10px] font-bold text-slate-400">آخر مزامنة</div>
                    <div className="mt-1 text-xs font-extrabold text-slate-700">{formatDate(integration.last_sync_at)}</div>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <div className="text-[10px] font-bold text-slate-400">جودة البيانات</div>
                    <div className="mt-1 text-sm font-extrabold text-slate-800">
                        {DATA_QUALITY[integration.health?.data_quality]
                            || integration.health?.data_quality
                            || "غير معروفة"}
                    </div>
                </div>
            </div>

            <section className="mt-4" aria-label="الحسابات الإعلانية الظاهرة">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <div className="text-xs font-extrabold text-slate-700">الحسابات</div>
                    <div className="text-[10px] font-bold text-slate-500">
                        صلاحيات حالية: {currentPermissions.length} · ناقصة: {missingPermissions.length}
                    </div>
                </div>
                {accounts.length ? (
                    <div className="space-y-2" data-testid={`advertising-account-rows-${integration.provider}`}>
                        {accounts.slice(0, 4).map((account, index) => {
                            const id = accountId(account);
                            return (
                                <div
                                    key={account.mezan_integration_account_id || id || index}
                                    className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 text-xs"
                                >
                                    <div className="min-w-0">
                                        <div className="truncate font-extrabold text-slate-800">
                                            {account.display_name || "حساب إعلاني"}
                                        </div>
                                        <div className="mt-1 truncate font-mono text-[10px] text-slate-400" dir="ltr">
                                            {id || "هوية الحساب غير متاحة"}
                                        </div>
                                    </div>
                                    <div className="shrink-0 text-left font-mono text-[10px] leading-5 text-slate-500">
                                        <div>{account.currency || "—"}</div>
                                        <div>{account.timezone || "—"}</div>
                                    </div>
                                </div>
                            );
                        })}
                        {accounts.length > 4 && (
                            <div className="text-center text-[11px] font-bold text-slate-400">
                                +{accounts.length - 4} حسابات أخرى
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-4 text-center text-xs text-slate-500">
                        {integration.connection_provenance === "data_feed"
                            ? "تصل بيانات محلية، لكن لا يوجد حساب API ظاهر يمكن اختياره."
                            : "لا توجد حسابات إعلانية مكتشفة داخل هذا الربط."}
                    </div>
                )}
            </section>

            {integration.latest_error && (
                <div className="mt-4 flex items-start gap-2 rounded-lg border border-rose-100 bg-rose-50 p-3 text-xs text-rose-800" data-testid="advertising-account-latest-error">
                    <WarningCircle size={17} className="mt-0.5 shrink-0" weight="fill" />
                    <div className="min-w-0">
                        <div className="font-extrabold">آخر مزامنة تحتاج مراجعة</div>
                        <div className="mt-1 break-words leading-5">
                            {integration.latest_error.message || integration.latest_error.code}
                        </div>
                    </div>
                </div>
            )}

            {hasProviderTools && (
                <details
                    className="group mt-4 rounded-xl border border-slate-200 bg-slate-50"
                    data-testid={`advertising-account-tools-${integration.provider}`}
                >
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-xs font-extrabold text-slate-800">
                        <span className="inline-flex items-center gap-2">
                            <CheckCircle size={16} weight="duotone" className="text-emerald-700" />
                            إدارة الحسابات والتقارير
                        </span>
                        <CaretDown size={16} weight="bold" className="transition-transform group-open:rotate-180" />
                    </summary>
                    <div className="border-t border-slate-200 bg-white p-3 sm:p-4">
                        <ProviderTools integration={integration} snapchatScope={snapchatScope} />
                    </div>
                </details>
            )}

            {hasActions && (
                <footer className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
                    <ActionButton
                        label={testing ? "جاري الفحص…" : "فحص محلي"}
                        Icon={ArrowClockwise}
                        enabled={canTest}
                        onClick={() => onTest?.(integration.provider)}
                        primary
                        testid={`integration-${integration.provider}-test`}
                    />
                    <ActionButton
                        label={syncing ? "جاري المزامنة…" : "مزامنة 30 يوم"}
                        Icon={ArrowClockwise}
                        enabled={canSync}
                        onClick={() => onSync?.(integration.provider)}
                        testid={`integration-${integration.provider}-sync`}
                    />
                    <ActionButton
                        label="إعادة الربط"
                        Icon={LinkSimple}
                        enabled={canRelink}
                        onClick={() => onSettings?.(integration.provider)}
                        testid={`integration-${integration.provider}-reconnect`}
                    />
                    <ActionButton
                        label="إعدادات"
                        Icon={Gear}
                        enabled={canOpenSettings}
                        onClick={() => onSettings?.(integration.provider)}
                        testid={`integration-${integration.provider}-settings`}
                    />
                </footer>
            )}
        </article>
    );
}
