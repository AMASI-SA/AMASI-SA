import CampaignManagerTable from "./CampaignManagerTable";
import AdSquadManagerTable from "./AdSquadManagerTable";
import AdSquadSortControls from "./AdSquadSortControls";
import AdManagerTable from "./AdManagerTable";
import SnapchatCampaignManagementPanel from "./SnapchatCampaignManagementPanel";
import SnapchatOrderSourceAudit from "./SnapchatOrderSourceAudit";

function EntityTabs({ platformLabel, entityLevel, onChange, adSquadsEnabled, adsEnabled, managementEnabled }) {
    const items = [
        { id: "campaigns", label: "الحملات", enabled: true },
        { id: "ad_squads", label: "المجموعات الإعلانية", enabled: adSquadsEnabled },
        { id: "ads", label: "الإعلانات", enabled: adsEnabled },
    ];
    return (
        <div className="flex min-h-14 items-end gap-6 overflow-x-auto rounded-t-2xl border border-b-0 border-slate-200 bg-white px-4" data-testid="ads-entity-level-tabs">
            {items.map((item) => {
                const active = entityLevel === item.id;
                return (
                    <button
                        key={item.id}
                        type="button"
                        disabled={!item.enabled}
                        onClick={() => item.enabled && onChange?.(item.id)}
                        className={["relative shrink-0 px-1 pb-3 pt-4 text-sm transition", active ? "font-black text-slate-950" : item.enabled ? "font-bold text-slate-600 hover:text-slate-950" : "cursor-not-allowed font-bold text-slate-300"].join(" ")}
                        aria-pressed={active}
                        data-testid={`ads-entity-level-${item.id}`}
                    >
                        {item.label}
                        {active && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-amber-400" />}
                    </button>
                );
            })}
            <span className="mr-auto pb-3 text-[11px] font-bold text-slate-400">
                {platformLabel || "المنصة"} · {managementEnabled ? "تقارير قراءة فقط · إدارة محكومة" : "قراءة فقط"}
            </span>
        </div>
    );
}

function ActiveCampaignFilter({ checked, onChange, entityLevel }) {
    const entityLabel = entityLevel === "ad_squads" ? "المجموعات" : entityLevel === "ads" ? "الإعلانات" : "الحملات";
    return (
        <div className="flex flex-wrap items-center justify-between gap-3 border-x border-t border-slate-200 bg-white px-4 py-3" data-testid="active-campaign-filter">
            <div>
                <div className="text-sm font-black text-slate-800">{checked ? `${entityLabel} التابعة لحملات نشطة فقط` : `عرض كل ${entityLabel}`}</div>
                <div className="mt-0.5 text-[11px] font-bold text-slate-400">الفلترة تتم من المصدر قبل Pagination، وليست فلترة للصفحة الحالية.</div>
            </div>
            <button
                type="button"
                onClick={() => onChange?.(!checked)}
                aria-pressed={checked}
                data-testid="active-campaigns-only-toggle"
                className={`inline-flex min-h-10 items-center gap-2 rounded-xl border px-4 text-sm font-black transition ${checked ? "border-emerald-300 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-white text-slate-700"}`}
            >
                <span className={`relative inline-flex h-5 w-9 rounded-full ${checked ? "bg-emerald-500" : "bg-slate-300"}`}>
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm ${checked ? "right-[18px]" : "right-0.5"}`} />
                </span>
                الحملات النشطة فقط
            </button>
        </div>
    );
}

function nativeAppliedDate(kind) {
    if (typeof document === "undefined") return null;
    return document.querySelector(`[data-mezan-native-date="${kind}"]`)?.value || null;
}

export default function AdsEntityLevelWorkspace({
    platform,
    platformLabel,
    resultSource = "salla",
    actionReportTime = "conversion",
    entityLevel,
    onEntityLevelChange,
    campaigns,
    campaignTotals,
    campaignPagination,
    campaignPage,
    onCampaignPageChange,
    campaignLoading = false,
    readOnly,
    activeCampaignsOnly = true,
    onActiveCampaignsOnlyChange,
    adSquadSort = "orders",
    onAdSquadSortChange,
    adSquadReport,
    adSquadPage,
    onAdSquadPageChange,
    adSquadLoading,
    adSquadError,
    selectedCampaign = null,
    selectedAdSquad = null,
    onOpenAdSquads,
    onOpenAds,
    onClearHierarchy,
    onManagementChanged,
}) {
    const adSquadsEnabled = platform === "snapchat";
    const adsEnabled = platform === "snapchat";
    const campaignReport = { campaigns, totals: campaignTotals };
    const auditAccountId = campaignReport.campaigns?.[0]?.account_id || campaigns?.[0]?.account_id || null;
    const auditDateFrom = nativeAppliedDate("from");
    const auditDateTo = nativeAppliedDate("to");

    return (
        <section data-testid="ads-entity-level-workspace">
            <style>{`
                [data-testid="ads-entity-level-workspace"] [data-testid="campaign-manager-table"] > div:first-child { display: none; }
                [data-testid="ads-entity-level-workspace"] [data-testid="campaign-manager-table"] { border-top-left-radius: 0; border-top-right-radius: 0; }
            `}</style>
            {platform === "snapchat" && (
                <SnapchatCampaignManagementPanel
                    accountId={auditAccountId}
                    entityLevel={entityLevel}
                    selectedCampaign={selectedCampaign}
                    selectedAdSquad={selectedAdSquad}
                    onChanged={onManagementChanged}
                />
            )}
            <EntityTabs platformLabel={platformLabel} entityLevel={entityLevel} onChange={onEntityLevelChange} adSquadsEnabled={adSquadsEnabled} adsEnabled={adsEnabled} managementEnabled={platform === "snapchat"} />
            {platform === "snapchat" && (selectedCampaign || selectedAdSquad) && (
                <div
                    className="flex flex-wrap items-center gap-2 border-x border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-bold text-slate-600"
                    data-testid="snapchat-entity-breadcrumb"
                >
                    <button
                        type="button"
                        onClick={onClearHierarchy}
                        className="rounded-lg bg-white px-3 py-1.5 text-emerald-700 shadow-sm hover:bg-emerald-50"
                    >
                        كل الحملات
                    </button>
                    <span>/</span>
                    {selectedCampaign && (
                        <button
                            type="button"
                            onClick={() => onOpenAdSquads?.(selectedCampaign)}
                            className="rounded-lg px-2 py-1.5 hover:bg-white hover:text-emerald-700"
                        >
                            {selectedCampaign.campaign_name || selectedCampaign.campaign_id}
                        </button>
                    )}
                    {selectedAdSquad && (
                        <>
                            <span>/</span>
                            <span className="rounded-lg bg-violet-50 px-2 py-1.5 text-violet-700">
                                {selectedAdSquad.ad_squad_name || selectedAdSquad.ad_squad_id}
                            </span>
                        </>
                    )}
                </div>
            )}
            {platform === "snapchat" && entityLevel === "campaigns" && (
                <SnapchatOrderSourceAudit
                    accountId={auditAccountId}
                    dateFrom={auditDateFrom}
                    dateTo={auditDateTo}
                />
            )}
            {platform === "snapchat" && (
                <ActiveCampaignFilter checked={activeCampaignsOnly} onChange={onActiveCampaignsOnlyChange} entityLevel={entityLevel} />
            )}
            {entityLevel === "campaigns" && (
                <CampaignManagerTable
                    platform={platform}
                    platformLabel={platformLabel}
                    resultSource={resultSource}
                    campaigns={campaignReport.campaigns}
                    totals={campaignReport.totals}
                    pagination={campaignPagination}
                    page={campaignPage}
                    onPageChange={onCampaignPageChange}
                    loading={campaignLoading}
                    readOnly={readOnly}
                    onOpenAdSquads={platform === "snapchat" ? onOpenAdSquads : undefined}
                />
            )}
            {entityLevel === "ad_squads" && (
                <>
                    <AdSquadSortControls value={adSquadSort} onChange={onAdSquadSortChange} />
                    <AdSquadManagerTable
                        rows={adSquadReport?.ad_squads || []}
                        totals={adSquadReport?.totals || {}}
                        pagination={adSquadReport?.pagination || {}}
                        page={adSquadPage}
                        onPageChange={onAdSquadPageChange}
                        loading={adSquadLoading}
                        error={adSquadError}
                        sortMode={adSquadSort}
                        onOpenAds={onOpenAds}
                    />
                </>
            )}
            {entityLevel === "ads" && (
                <AdManagerTable
                    activeCampaignsOnly={activeCampaignsOnly}
                    actionReportTime={actionReportTime}
                    campaignId={selectedCampaign?.campaign_id || null}
                    adSquadId={selectedAdSquad?.ad_squad_id || null}
                />
            )}
        </section>
    );
}
