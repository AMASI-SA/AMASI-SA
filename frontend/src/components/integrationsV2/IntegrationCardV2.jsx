import {
    ArrowClockwise,
    CheckCircle,
    Gear,
    LinkSimple,
    Prohibit,
    WarningCircle,
    XCircle,
} from "@phosphor-icons/react";
import ProviderMark from "./ProviderMark";
import SnapchatAccountScope from "./SnapchatAccountScope";
import MetaReportingControl from "./MetaReportingControl";
import { CONNECTION_PROVENANCE_LABELS } from "../../services/integrationsV2";

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
    api_connection: [
        CONNECTION_PROVENANCE_LABELS.api_connection,
        "border-emerald-200 bg-emerald-50 text-emerald-700",
    ],
    legacy_integration: [
        CONNECTION_PROVENANCE_LABELS.legacy_integration,
        "border-amber-200 bg-amber-50 text-amber-800",
    ],
    data_feed: [
        CONNECTION_PROVENANCE_LABELS.data_feed,
        "border-sky-200 bg-sky-50 text-sky-700",
    ],
    disconnected: [
        CONNECTION_PROVENANCE_LABELS.disconnected,
        "border-slate-200 bg-slate-50 text-slate-600",
    ],
    planned: [
        CONNECTION_PROVENANCE_LABELS.planned,
        "border-violet-200 bg-violet-50 text-violet-700",
    ],
    unknown: [
        CONNECTION_PROVENANCE_LABELS.unknown,
        "border-slate-200 bg-slate-50 text-slate-600",
    ],
};

const PROVENANCE_DESCRIPTION = {
    api_connection: "اتصال مباشر محفوظ بواجهة المنصة",
    legacy_integration: "موصل سابق قائم خارج المركز الجديد",
    data_feed: "بيانات محلية دون اتصال إدارة API",
    disconnected: "لا توجد أدلة ربط أو تغذية بيانات",
    planned: "الموصل ضمن الخطة المستقبلية",
    unknown: "لا تكفي الأدلة لتحديد نوع الاتصال",
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

function formatDelay(value) {
    if (value === null || value === undefined) return "غير معروف";
    if (value < 60) return `${Math.round(value)} دقيقة`;
    if (value < 1440) return `${Math.round(value / 60)} ساعة`;
    return `${Math.round(value / 1440)} يوم`;
}

function isTrackingDiagnostic(error) {
    const code = String(error?.code || "").trim().toLowerCase();
    return code.startsWith("snapchat_tracking_");
}

function trackingDiagnosticMessage(error) {
    const code = String(error?.code || "").trim().toLowerCase();
    if (code === "snapchat_tracking_diagnostics_partial") {
        return "اكتملت مزامنة الحملات والمصروفات، لكن بعض نقاط تشخيص Pixel غير متاحة. أبقى ميزان القيم غير المعروفة فارغة بدل تحويلها إلى صفر.";
    }
    if (code === "snapchat_tracking_http_400") {
        return "تعذر فحص إحدى نقاط Pixel لهذا الحساب. لا يؤثر ذلك في مزامنة الحملات أو المصروفات، وتبقى القيمة غير المعروفة فارغة.";
    }
    return "توجد ملاحظة محدودة في تشخيص Pixel، ولا تعني فشل مزامنة الحملات أو المصروفات.";
}

function ScoreBar({ label, score, testid }) {
    const known = score !== null && score !== undefined;
    const value = known ? Math.max(0, Math.min(100, Number(score))) : 0;
    const color = value >= 80 ? "bg-emerald-500" : value >= 55 ? "bg-amber-500" : "bg-rose-500";
    return (
        <div
            data-testid={testid}
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={known ? Math.round(value) : undefined}
            aria-valuetext={known ? `${Math.round(value)}%` : "غير معروف"}
        >
            <div className="mb-1 flex items-center justify-between text-xs">
                <span className="font-bold text-slate-600">{label}</span>
                <span className="font-mono font-bold text-slate-800">
                    {known ? `${Math.round(value)}%` : "غير معروف"}
                </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                {known && <div className={`h-full rounded-full ${color}`} style={{ width: `${value}%` }} />}
            </div>
        </div>
    );
}

function PermissionChips({ title, items, tone, emptyLabel }) {
    const palette = tone === "missing"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : tone === "unknown"
            ? "border-slate-200 bg-slate-50 text-slate-600"
            : "border-emerald-200 bg-emerald-50 text-emerald-700";
    return (
        <div>
            <div className="mb-2 text-xs font-extrabold text-slate-600">{title}</div>
            <div className="flex flex-wrap gap-1.5">
                {(items || []).length ? items.map((item) => (
                    <span
                        key={item}
                        className={`rounded-full border px-2 py-1 font-mono text-[11px] ${palette}`}
                    >
                        {item}
                    </span>
                )) : (
                    <span className="text-xs text-slate-400">{emptyLabel}</span>
                )}
            </div>
        </div>
    );
}

function AiList({ title, items, positive }) {
    const Icon = positive ? CheckCircle : XCircle;
    const color = positive ? "text-emerald-600" : "text-slate-500";
    return (
        <div className="rounded-xl border border-slate-100 bg-slate-50 p-3">
            <div className="mb-2 text-xs font-extrabold text-slate-700">{title}</div>
            {(items || []).length ? (
                <ul className="space-y-1.5">
                    {items.slice(0, 4).map((item) => (
                        <li key={item} className="flex items-start gap-2 text-xs leading-5 text-slate-600">
                            <Icon size={16} className={`mt-0.5 shrink-0 ${color}`} weight="fill" />
                            <span>{item}</span>
                        </li>
                    ))}
                </ul>
            ) : <div className="text-xs text-slate-400">لا توجد قدرة مثبتة بعد.</div>}
        </div>
    );
}

function ActionButton({
    provider,
    action,
    label,
    Icon,
    enabled,
    reason,
    onClick,
    primary = false,
}) {
    const accessibleLabel = !enabled && reason ? `${label}: ${reason}` : label;
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={!enabled}
            title={!enabled ? (reason || "غير متاح في المرحلة الأولى") : undefined}
            aria-label={accessibleLabel}
            className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border px-3 text-xs font-extrabold transition ${
                enabled
                    ? primary
                        ? "border-emerald-800 bg-emerald-800 text-white hover:bg-emerald-900"
                        : "border-slate-200 bg-white text-slate-700 hover:border-emerald-300 hover:text-emerald-800"
                    : "cursor-not-allowed border-slate-100 bg-slate-50 text-slate-400"
            }`}
            data-testid={`integration-${provider}-${action}`}
        >
            <Icon size={16} weight="bold" />
            {label}
        </button>
    );
}

export default function IntegrationCard({
    integration,
    testing = false,
    syncing = false,
    settingsAvailable = false,
    snapchatScope = null,
    onTest,
    onSync,
    onSettings,
}) {
    const [statusLabel, statusClass] = STATUS[integration.connection_status] || STATUS.unknown;
    const [provenanceLabel, provenanceClass] = (
        PROVENANCE[integration.connection_provenance] || PROVENANCE.unknown
    );
    const healthScore = integration.health?.score;
    const canTest = Boolean(integration.actions?.test_connection?.enabled) && !testing;
    const showSync = integration.provider === "snapchat_ads";
    const showMetaReporting = integration.provider === "meta_ads";
    const canSync = showSync
        && Boolean(integration.actions?.sync_data?.enabled)
        && !syncing;
    const canRelink = settingsAvailable && Boolean(integration.actions?.reconnect?.enabled);
    const canOpenSettings = settingsAvailable && Boolean(integration.actions?.settings?.enabled);
    const primaryAccount = integration.accounts?.[0];
    const trackingDiagnostic = showSync && isTrackingDiagnostic(integration.latest_error);
    const syncComplete = showSync
        && Boolean(integration.last_sync_at)
        && ["complete", "good", "healthy"].includes(
            String(integration.health?.data_quality || "").toLowerCase(),
        );
    const showOperationalStatus = ![
        "connected",
        "data_available",
        "not_connected",
        "not_configured",
        "planned",
    ].includes(integration.connection_status);
    const permissionsUnknown = Boolean(integration.permissions?.unknown);
    const noApiPermissionContext = ["data_feed", "disconnected", "planned"].includes(
        integration.connection_provenance,
    );
    const currentPermissionsEmptyLabel = permissionsUnknown
        ? "لم نتمكن من التحقق من الصلاحيات"
        : noApiPermissionContext
            ? "تظهر الصلاحيات بعد ربط API"
            : "لا توجد صلاحيات مثبتة";
    const missingPermissionsEmptyLabel = permissionsUnknown
        ? "لا يوجد نقص مؤكد؛ التحقق غير متاح"
        : noApiPermissionContext
            ? "لا تُحسب قبل وجود ربط API"
            : "لا يوجد نقص مثبت";

    return (
        <article
            className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-4 transition hover:-translate-y-0.5 hover:shadow-sm sm:p-5"
            data-testid={`integration-card-${integration.provider}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                    <ProviderMark provider={integration.provider} />
                    <div className="min-w-0">
                        <h2 className="truncate text-lg font-extrabold tracking-tight text-slate-950">
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
                    {showOperationalStatus && (
                        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${statusClass}`}>
                            {statusLabel}
                        </span>
                    )}
                </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs">
                <div>
                    <div className="text-slate-400">الحساب أو المتجر</div>
                    <div className="mt-1 truncate font-bold text-slate-800">
                        {showSync
                            ? `${integration.accounts?.length || 0} حسابات مكتشفة`
                            : primaryAccount?.display_name || integration.accounts?.length
                                ? primaryAccount?.display_name || `${integration.accounts.length} حساب`
                                : integration.connection_provenance === "data_feed"
                                    ? "تصل بيانات دون حساب API مرتبط"
                                    : "لا يوجد حساب مرتبط"}
                    </div>
                </div>
                <div>
                    <div className="text-slate-400">نوع الاتصال</div>
                    <div className="mt-1 font-bold text-slate-700">
                        {provenanceLabel}
                    </div>
                    <div className="mt-0.5 text-[10px] leading-4 text-slate-400">
                        {PROVENANCE_DESCRIPTION[integration.connection_provenance]
                            || PROVENANCE_DESCRIPTION.unknown}
                    </div>
                </div>
                <div>
                    <div className="text-slate-400">آخر مزامنة</div>
                    <div className="mt-1 font-bold text-slate-700">{formatDate(integration.last_sync_at)}</div>
                </div>
                <div>
                    <div className="text-slate-400">تأخر البيانات</div>
                    <div className="mt-1 font-bold text-slate-700">
                        {formatDelay(integration.data_delay_minutes)}
                    </div>
                </div>
            </div>

            {showSync ? (
                <SnapchatAccountScope
                    accounts={integration.accounts}
                    initialSelection={snapchatScope?.selection || null}
                    initialSummary={snapchatScope?.summary || null}
                />
            ) : (integration.accounts || []).length > 0 && (
                <div className="mt-3 space-y-2" data-testid={`integration-accounts-${integration.provider}`}>
                    {integration.accounts.slice(0, 3).map((account, index) => (
                        <div
                            key={account.mezan_integration_account_id || `${integration.provider}-${index}`}
                            className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2 text-xs"
                        >
                            <div className="min-w-0">
                                <div className="truncate font-bold text-slate-700">
                                    {account.display_name || "حساب مرتبط"}
                                </div>
                                <div className="truncate font-mono text-[11px] text-slate-400">
                                    {account.external_account_id || account.store_id || account.ad_account_id || "هوية غير متاحة"}
                                </div>
                            </div>
                            <div className="shrink-0 text-left font-mono text-[11px] text-slate-500">
                                <div>{account.currency || "—"}</div>
                                <div>{account.timezone || "—"}</div>
                            </div>
                        </div>
                    ))}
                    {integration.accounts.length > 3 && (
                        <div className="text-center text-xs text-slate-400">
                            +{integration.accounts.length - 3} حسابات أخرى
                        </div>
                    )}
                </div>
            )}

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <ScoreBar
                    label="صحة التكامل"
                    score={healthScore}
                    testid={`integration-health-${integration.provider}`}
                />
                <div>
                    <div className="mb-1 text-xs font-bold text-slate-600">جودة البيانات</div>
                    <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs font-extrabold text-slate-700">
                        {DATA_QUALITY[integration.health?.data_quality] || integration.health?.data_quality || "غير معروفة"}
                    </div>
                </div>
            </div>

            {showSync && (
                <div className="mt-4 grid gap-2 sm:grid-cols-2" data-testid="snapchat-status-separation">
                    <div className={`rounded-xl border p-3 ${
                        syncComplete
                            ? "border-emerald-100 bg-emerald-50 text-emerald-800"
                            : "border-slate-200 bg-slate-50 text-slate-700"
                    }`}>
                        <div className="flex items-center justify-between gap-2 text-xs font-extrabold">
                            <span>مزامنة الحملات والمصروفات</span>
                            <span>{syncComplete ? "مكتملة" : "تحتاج تحقق"}</span>
                        </div>
                        <div className="mt-1 text-[11px] leading-5 opacity-80">
                            {syncComplete
                                ? "تم تحديث بيانات الحسابات المحددة دون كتابة محاسبية."
                                : "لا توجد مزامنة مكتملة حديثة مثبتة في البطاقة."}
                        </div>
                    </div>
                    <div className={`rounded-xl border p-3 ${
                        trackingDiagnostic
                            ? "border-amber-200 bg-amber-50 text-amber-900"
                            : "border-emerald-100 bg-emerald-50 text-emerald-800"
                    }`}>
                        <div className="flex items-center justify-between gap-2 text-xs font-extrabold">
                            <span>تشخيص Pixel</span>
                            <span>{trackingDiagnostic ? "جزئي" : "لا توجد ملاحظة حديثة"}</span>
                        </div>
                        <div className="mt-1 text-[11px] leading-5 opacity-80">
                            {trackingDiagnostic
                                ? "حالة مستقلة عن مزامنة الحملات والمصروفات."
                                : "لم تُسجل ملاحظة تشخيص محدودة في آخر حالة معروضة."}
                        </div>
                    </div>
                </div>
            )}

            {integration.latest_error && (
                <div className={`mt-4 flex items-start gap-2 rounded-xl border p-3 text-xs ${
                    trackingDiagnostic
                        ? "border-amber-200 bg-amber-50 text-amber-900"
                        : "border-rose-100 bg-rose-50 text-rose-800"
                }`} data-testid={trackingDiagnostic ? "snapchat-tracking-notice" : "integration-latest-error"}>
                    <WarningCircle
                        size={18}
                        className={`mt-0.5 shrink-0 ${trackingDiagnostic ? "text-amber-600" : "text-rose-600"}`}
                        weight="fill"
                    />
                    <div className="min-w-0">
                        <div className="font-extrabold">
                            {trackingDiagnostic ? "ملاحظة تشخيص Pixel" : "آخر خطأ"}
                        </div>
                        <div className="mt-1 break-words leading-5">
                            {trackingDiagnostic
                                ? trackingDiagnosticMessage(integration.latest_error)
                                : integration.latest_error.message || integration.latest_error.code}
                        </div>
                        {trackingDiagnostic && (
                            <div className="mt-1 font-mono text-[10px] text-amber-700">
                                {integration.latest_error.code}
                            </div>
                        )}
                    </div>
                </div>
            )}

            <div className="mt-4 grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-2">
                <PermissionChips
                    title="الصلاحيات الحالية"
                    items={integration.permissions?.current}
                    tone={permissionsUnknown ? "unknown" : undefined}
                    emptyLabel={currentPermissionsEmptyLabel}
                />
                <PermissionChips
                    title="الصلاحيات الناقصة"
                    items={integration.permissions?.missing}
                    tone={permissionsUnknown ? "unknown" : "missing"}
                    emptyLabel={missingPermissionsEmptyLabel}
                />
            </div>

            <div className="mt-4 grid gap-2 sm:grid-cols-2">
                <AiList title="ما يستطيع الذكاء الاصطناعي فعله" items={integration.ai?.can} positive />
                <AiList title="ما لا يستطيع فعله الآن" items={integration.ai?.cannot} />
            </div>

            {showMetaReporting && (
                <div className="mt-4" data-testid="meta-reporting-control-host">
                    <MetaReportingControl integration={integration} />
                </div>
            )}

            <div className={`mt-auto grid grid-cols-2 gap-2 border-t border-slate-100 pt-4 ${
                showSync ? "lg:grid-cols-5" : "lg:grid-cols-4"
            }`}>
                <ActionButton
                    provider={integration.provider}
                    action="test"
                    label={testing ? "جاري الفحص…" : "فحص محلي"}
                    Icon={ArrowClockwise}
                    enabled={canTest}
                    reason={integration.actions?.test_connection?.reason}
                    onClick={() => onTest(integration.provider)}
                    primary
                />
                {showSync && (
                    <ActionButton
                        provider={integration.provider}
                        action="sync"
                        label={syncing ? "جاري المزامنة…" : "مزامنة 30 يوم"}
                        Icon={ArrowClockwise}
                        enabled={canSync}
                        reason={integration.actions?.sync_data?.reason}
                        onClick={() => onSync?.(integration.provider)}
                    />
                )}
                <ActionButton
                    provider={integration.provider}
                    action="reconnect"
                    label="إعادة الربط"
                    Icon={LinkSimple}
                    enabled={canRelink}
                    reason={integration.actions?.reconnect?.reason}
                    onClick={() => onSettings(integration.provider)}
                />
                <ActionButton
                    provider={integration.provider}
                    action="settings"
                    label="إعدادات"
                    Icon={Gear}
                    enabled={canOpenSettings}
                    reason={integration.actions?.settings?.reason}
                    onClick={() => onSettings(integration.provider)}
                />
                <ActionButton
                    provider={integration.provider}
                    action="disconnect"
                    label="إلغاء الربط"
                    Icon={Prohibit}
                    enabled={false}
                    reason="محمي في المرحلة الأولى؛ لا تُحذف Tokens أو إعدادات الربط من هذا المركز."
                />
            </div>
        </article>
    );
}
