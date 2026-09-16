import { snapchatBidLabel } from "../../services/snapchatCampaignManagement";

function unavailable(settings, loading) {
    const status = settings?.quality?.settings_status;
    if (status === "settings_complete") return null;
    const label = status === "settings_stale" ? "إعدادات قديمة"
        : status === "settings_sync_failed" ? "تعذّر جلب الإعدادات"
            : loading ? "جارٍ التحميل…" : "غير متاح";
    return <span className="text-slate-500" title={settings?.quality?.reason}>{label}</span>;
}

function amount(settings, value) {
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "غير متاح";
    if (!settings.account_currency) return "عملة الحساب غير متاحة";
    return <span className="whitespace-nowrap font-mono font-bold" dir="ltr">{(Number(value) / 1_000_000).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })} {settings.account_currency}</span>;
}

// Opt-in columns keep other report consumers unchanged. Only the visible page
// supplies settings; absent/stale values must never become a numeric zero.
export function snapchatInlineColumns({ level, settingsByEntityId, loading, parentCampaign }) {
    if (!["campaign", "ad_group"].includes(level)) return [];
    const settingsFor = (row) => {
        const settings = settingsByEntityId[row.entity.id];
        return settings?.unified_entity_id === row.entity.id ? settings : null;
    };
    const columns = [{
        key: "daily-budget",
        label: level === "campaign" ? "ميزانية الحملة اليومية" : "ميزانية المجموعة اليومية",
        render: (row) => {
            const settings = settingsFor(row);
            return unavailable(settings, loading) || (settings.daily_budget_availability === "unsupported_at_provider_level"
                ? "غير متاح على مستوى الحملة" : amount(settings, settings.daily_budget_micro));
        },
    }];
    if (level === "ad_group") {
        columns.push({
            key: "bid-target-cost", label: "Target Cost / Bid",
            render: (row) => {
                const settings = settingsFor(row);
                const missing = unavailable(settings, loading);
                if (missing) return missing;
                const strategy = settings.bid_strategy;
                if (strategy === "AUTO_BID") return "مزايدة تلقائية";
                if (!strategy) return "غير متاح";
                return <div className="whitespace-nowrap"><div className="text-[10px] text-slate-500">{snapchatBidLabel(strategy)}</div>{amount(settings, settings.bid_micro)}</div>;
            },
        }, {
            key: "parent-campaign", label: "الحملة المرتبطة",
            render: (row) => {
                const id = row.entity.campaign_id;
                return <span className="block max-w-[220px] truncate" title={id || ""}>{id && parentCampaign?.entity?.id === id ? parentCampaign.entity.name : id || "غير متاح"}</span>;
            },
        });
    }
    return columns;
}
