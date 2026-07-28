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

const DATA_QUALITY = {
    good: "جيدة",
    healthy: "جيدة",
    stale: "متأخرة",
    degraded: "تحتاج مراجعة",
    missing: "لا توجد بيانات",
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

function ScoreBar({ label, score, testid }) {
    const known = score !== null && score !== undefined;
    const value = known ? Math.max(0, Math.min(100, Number(score))) : 0;
    const color = value >= 80 ? "bg-emerald-500" : value >= 55 ? "bg-amber-500" : "bg-rose-500";
    return (
        <div data-testid={testid}>
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
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={!enabled}
            title={!enabled ? (reason || "غير متاح في المرحلة الأولى") : undefined}
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
    settingsAvailable = false,
    onTest,
    onSettings,
}) {
    const [statusLabel, statusClass] = STATUS[integration.connection_status] || STATUS.unknown;
    const healthScore = integration.health?.score;
    const canTest = Boolean(integration.actions?.test_connection?.enabled) && !testing;
    const canRelink = settingsAvailable && Boolean(integration.actions?.reconnect?.enabled);
    const canOpenSettings = settingsAvailable && Boolean(integration.actions?.settings?.enabled);
    const primaryAccount = integration.accounts?.[0];

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
                <span className={`shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-extrabold ${statusClass}`}>
                    {statusLabel}
                </span>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs">
                <div>
                    <div className="text-slate-400">الحساب أو المتجر</div>
                    <div className="mt-1 truncate font-bold text-slate-800">
                        {primaryAccount?.display_name || integration.accounts?.length
                            ? primaryAccount?.display_name || `${integration.accounts.length} حساب`
                            : "لا يوجد حساب مرتبط"}
                    </div>
                </div>
                <div>
                    <div className="text-slate-400">مصدر الربط</div>
                    <div className="mt-1 truncate font-mono font-bold text-slate-700">
                        {integration.source_mode || "unknown"}
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

            {(integration.accounts || []).length > 0 && (
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

            {integration.latest_error && (
                <div className="mt-4 flex items-start gap-2 rounded-xl border border-rose-100 bg-rose-50 p-3 text-xs text-rose-800">
                    <WarningCircle size={18} className="mt-0.5 shrink-0" weight="fill" />
                    <div className="min-w-0">
                        <div className="font-extrabold">آخر خطأ</div>
                        <div className="mt-1 break-words leading-5">
                            {integration.latest_error.message || integration.latest_error.code}
                        </div>
                    </div>
                </div>
            )}

            <div className="mt-4 grid gap-4 border-t border-slate-100 pt-4 sm:grid-cols-2">
                <PermissionChips
                    title="الصلاحيات الحالية"
                    items={integration.permissions?.current}
                    emptyLabel={integration.permissions?.unknown ? "غير معروفة" : "لا توجد صلاحيات مثبتة"}
                />
                <PermissionChips
                    title="الصلاحيات الناقصة"
                    items={integration.permissions?.missing}
                    tone="missing"
                    emptyLabel="لا يوجد نقص مثبت"
                />
            </div>

            <div className="mt-4 grid gap-2 sm:grid-cols-2">
                <AiList title="ما يستطيع الذكاء الاصطناعي فعله" items={integration.ai?.can} positive />
                <AiList title="ما لا يستطيع فعله الآن" items={integration.ai?.cannot} />
            </div>

            <div className="mt-auto grid grid-cols-2 gap-2 border-t border-slate-100 pt-4 lg:grid-cols-4">
                <ActionButton
                    provider={integration.provider}
                    action="test"
                    label={testing ? "جاري الاختبار…" : "اختبار"}
                    Icon={ArrowClockwise}
                    enabled={canTest}
                    reason={integration.actions?.test_connection?.reason}
                    onClick={() => onTest(integration.provider)}
                    primary
                />
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
