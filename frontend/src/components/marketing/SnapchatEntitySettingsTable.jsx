import { snapchatBidLabel } from "../../services/snapchatCampaignManagement";

export const SNAPCHAT_SETTINGS_UNAVAILABLE = "غير متاح — فشل جلب الإعدادات";
export const SNAPCHAT_CAMPAIGN_BUDGET_UNSUPPORTED = "غير متاح من Snapchat على هذا المستوى";

const SETTINGS_STATUS_LABELS = {
    settings_not_loaded: "settings_not_loaded · لم تُحمّل الإعدادات",
    settings_sync_failed: "settings_sync_failed · فشلت المزامنة",
    settings_stale: "settings_stale · الإعدادات قديمة",
    settings_complete: "settings_complete · قراءة مكتملة",
};

function number(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}

function timestamp(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("ar-SA");
}

function providerSetting(settings, value) {
    if (settings?.quality?.settings_status !== "settings_complete") return SNAPCHAT_SETTINGS_UNAVAILABLE;
    return providerValue(value);
}

function providerValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function freshness(value) {
    if (value === null || value === undefined || value === "") return "غير متاح";
    const seconds = Number(value);
    return Number.isFinite(seconds) ? `${seconds.toLocaleString("en-US")} ثانية` : "غير متاح";
}

function microAmount(settings, microValue) {
    if (settings?.quality?.settings_status !== "settings_complete") {
        return { raw: SNAPCHAT_SETTINGS_UNAVAILABLE, converted: SNAPCHAT_SETTINGS_UNAVAILABLE };
    }
    if (microValue === null || microValue === undefined || microValue === "") {
        return { raw: SNAPCHAT_SETTINGS_UNAVAILABLE, converted: SNAPCHAT_SETTINGS_UNAVAILABLE };
    }
    const micro = Number(microValue);
    if (!Number.isFinite(micro)) {
        return { raw: SNAPCHAT_SETTINGS_UNAVAILABLE, converted: SNAPCHAT_SETTINGS_UNAVAILABLE };
    }
    const currency = String(settings?.account_currency || "").toUpperCase();
    const raw = `${micro.toLocaleString("en-US")} micro`;
    if (currency === "USD") {
        const usd = micro / 1_000_000;
        return {
            raw,
            converted: `${usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })} USD`,
        };
    }
    const native = micro / 1_000_000;
    return {
        raw,
        converted: currency
            ? `${native.toLocaleString("en-US", { maximumFractionDigits: 6 })} ${currency} · USD غير متاح`
            : "عملة الحساب غير متاحة · USD غير متاح",
    };
}

export function snapchatPerformanceStatus(row) {
    const explicit = String(row?.quality?.performance_status || "");
    if (["performance_complete", "performance_no_facts", "performance_partial"].includes(explicit)) return explicit;
    const syncStatus = String(row?.quality?.sync_status || "");
    const sourceFactCount = row?.quality?.source_fact_count;
    if (
        syncStatus === "no_facts"
        || (
            sourceFactCount !== null
            && sourceFactCount !== undefined
            && Number(sourceFactCount) === 0
        )
    ) return "performance_no_facts";
    if (syncStatus === "complete" || row?.quality?.coverage_status === "complete") return "performance_complete";
    return "performance_partial";
}

function Metric({ label, children, testId, dir = "ltr" }) {
    return (
        <div data-testid={testId}>
            <span className="block text-[10px] font-black text-slate-400">{label}</span>
            <span className="mt-0.5 block break-words font-mono text-[11px] font-bold text-slate-800" dir={dir}>{children}</span>
        </div>
    );
}

function SettingsStatus({ row, settings, loading }) {
    const quality = settings?.quality || {};
    const status = String(quality.settings_status || "settings_not_loaded");
    const complete = status === "settings_complete";
    const performance = snapchatPerformanceStatus(row);
    return (
        <div className="space-y-1">
            <span className={`block rounded-full px-2 py-1 text-[10px] font-black ${performance === "performance_complete" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>
                {performance}
            </span>
            <span className={`block rounded-full px-2 py-1 text-[10px] font-black ${complete ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
                {loading && !settings ? "settings_not_loaded · جارٍ تحميل الإعدادات" : SETTINGS_STATUS_LABELS[status] || status}
            </span>
        </div>
    );
}

function SettingsDetails({ row, settings }) {
    const type = row?.entity?.level === "ad_group" ? "ad_squad" : "campaign";
    const quality = settings?.quality || {};
    const settingsComplete = settings?.quality?.settings_status === "settings_complete";
    const budgetUnsupported = type === "campaign"
        && settingsComplete
        && ["unsupported_at_provider_level", "unsupported"].includes(settings?.daily_budget_availability);
    const campaignBudgetUnavailable = settings?.daily_budget_unavailable_message_ar
        || SNAPCHAT_CAMPAIGN_BUDGET_UNSUPPORTED;
    const budget = budgetUnsupported
        ? { raw: campaignBudgetUnavailable, converted: campaignBudgetUnavailable }
        : microAmount(settings, settings?.daily_budget_micro, settings?.daily_budget_usd);
    const childBudget = microAmount(
        settings,
        settings?.ad_squads_daily_budget_micro,
        settings?.ad_squads_daily_budget_usd,
    );
    const bid = microAmount(settings, settings?.bid_micro, settings?.bid_usd);
    const effectiveBidStrategy = settingsComplete ? settings?.bid_strategy : null;
    const strategies = settingsComplete
        ? Array.isArray(settings?.ad_squad_bid_strategies)
            ? settings.ad_squad_bid_strategies.join("، ")
            : settings?.campaign_bid_strategy || settings?.bid_strategy || "—"
        : SNAPCHAT_SETTINGS_UNAVAILABLE;
    return (
        <>
            <td className="px-4 py-4 align-top">
                <div className="max-w-[240px]">
                    <div className="truncate text-sm font-black text-slate-950" title={row?.entity?.name}>{row?.entity?.name || "—"}</div>
                    <div className="mt-1 font-mono text-[10px] text-slate-400" dir="ltr">{row?.entity?.id || "—"}</div>
                </div>
            </td>
            <td className="px-4 py-4 align-top"><SettingsStatus row={row} settings={settings} /></td>
            <td className="px-4 py-4 align-top">
                <div className="w-[280px] space-y-2 rounded-xl border border-slate-100 bg-slate-50 p-2">
                    <Metric label="Unified ID">{settings?.unified_entity_id || row?.entity?.id || "—"}</Metric>
                    <Metric label="Snapchat provider ID">{settings?.provider_entity_id || SNAPCHAT_SETTINGS_UNAVAILABLE}</Metric>
                    <Metric label="عملة الحساب">{settings?.account_currency || "غير متاحة"}</Metric>
                    <Metric label="mapping_status">{settings?.mapping_status || (settings?.mapping_verified ? "verified" : "غير متاح")}</Metric>
                </div>
            </td>
            <td className="px-4 py-4 align-top">
                <div className="w-[300px] grid grid-cols-2 gap-2 rounded-xl border border-slate-100 bg-slate-50 p-2">
                    <Metric label="الميزانية اليومية الخام" testId="snapchat-settings-daily-budget-raw">{budget.raw}</Metric>
                    <Metric label="الميزانية اليومية المحولة">{budget.converted}</Metric>
                    {type === "campaign" && (
                        <>
                            <Metric label="مجموع ميزانيات Ad Squads الخام">{childBudget.raw}</Metric>
                            <Metric label="مجموع ميزانيات Ad Squads">{childBudget.converted}</Metric>
                            <Metric label="Ad Squads النشطة">{number(settings?.active_ad_squads)}</Metric>
                        </>
                    )}
                </div>
            </td>
            <td className="px-4 py-4 align-top">
                <div className="w-[300px] grid grid-cols-2 gap-2 rounded-xl border border-slate-100 bg-slate-50 p-2">
                    {type === "campaign" ? (
                        <Metric label="استراتيجيات المزايدة">{strategies}</Metric>
                    ) : (
                        <>
                            <Metric label={`${snapchatBidLabel(effectiveBidStrategy)} الخام`} testId="snapchat-settings-bid-label">{bid.raw}</Metric>
                            <Metric label={snapchatBidLabel(settings?.bid_strategy)}>{bid.converted}</Metric>
                            <Metric label="bid_strategy">{providerSetting(settings, settings?.bid_strategy)}</Metric>
                            <Metric label="optimization_goal">{providerSetting(settings, settings?.optimization_goal)}</Metric>
                            <Metric label="billing_event">{providerSetting(settings, settings?.billing_event)}</Metric>
                            <Metric label="conversion_window">{providerSetting(settings, settings?.conversion_window)}</Metric>
                        </>
                    )}
                </div>
            </td>
            <td className="px-4 py-4 align-top">
                <div className="w-[300px] grid grid-cols-2 gap-2 rounded-xl border border-slate-100 bg-slate-50 p-2">
                    <Metric label="الحالة الحالية">{providerSetting(settings, settings?.status)}</Metric>
                    <Metric label="freshness">{freshness(quality.freshness_seconds)}</Metric>
                    <Metric label="freshness threshold">{freshness(quality.freshness_threshold_seconds)}</Metric>
                    <Metric label="سبب الجودة" dir="rtl">{quality.reason || SNAPCHAT_SETTINGS_UNAVAILABLE}</Metric>
                    <Metric label="settings_synced_at" dir="rtl">{timestamp(settings?.settings_synced_at)}</Metric>
                    <Metric label="provider_updated_at" dir="rtl">{timestamp(settings?.provider_updated_at)}</Metric>
                </div>
            </td>
        </>
    );
}

export default function SnapchatEntitySettingsTable({
    report,
    settingsByEntityId = {},
    loading = false,
}) {
    const rows = (report?.rows || []).filter((row) => ["campaign", "ad_group"].includes(row?.entity?.level));
    if (!rows.length && !loading) return null;
    return (
        <section className="mt-4 overflow-hidden rounded-2xl border border-sky-200 bg-white" data-testid="snapchat-entity-settings-table">
            <header className="border-b border-sky-100 bg-sky-50 px-4 py-4">
                <h3 className="text-lg font-black text-slate-950">إعدادات Campaign / Ad Squad من Snapchat</h3>
                <p className="mt-1 text-xs font-bold text-slate-600">قراءة entity/settings المنفصلة عن reporting وfacts؛ Missing provider field لا يُعرض صفرًا.</p>
            </header>
            <div className="overflow-x-auto">
                <table className="min-w-[1850px] w-full text-right text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                        <tr>
                            <th className="px-4 py-3 font-black">الكيان</th>
                            <th className="px-4 py-3 font-black">حالة الأداء / الإعدادات</th>
                            <th className="px-4 py-3 font-black">مطابقة المعرف</th>
                            <th className="px-4 py-3 font-black">الميزانية</th>
                            <th className="px-4 py-3 font-black">المزايدة</th>
                            <th className="px-4 py-3 font-black">الجودة والمزامنة</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {rows.map((row) => (
                            <tr key={`${row.entity.level}:${row.entity.id}`} data-testid={`snapchat-settings-${row.entity.id}`}>
                                <SettingsDetails row={row} settings={settingsByEntityId[row.entity.id]} />
                            </tr>
                        ))}
                        {!rows.length && loading && (
                            <tr><td colSpan={6} className="px-4 py-10 text-center font-black text-slate-400">جارٍ تحميل إعدادات Snapchat…</td></tr>
                        )}
                    </tbody>
                </table>
            </div>
        </section>
    );
}
