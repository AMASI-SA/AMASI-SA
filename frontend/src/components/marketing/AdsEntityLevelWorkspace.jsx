import CampaignManagerTable from "./CampaignManagerTable";
import AdSquadManagerTable from "./AdSquadManagerTable";
import AdSquadSortControls from "./AdSquadSortControls";
import AdManagerTable from "./AdManagerTable";

function EntityTabs({
    platformLabel,
    entityLevel,
    onChange,
    adSquadsEnabled,
    adsEnabled,
}) {
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
                        className={[
                            "relative shrink-0 px-1 pb-3 pt-4 text-sm transition",
                            active
                                ? "font-black text-slate-950"
                                : item.enabled
                                    ? "font-bold text-slate-600 hover:text-slate-950"
                                    : "cursor-not-allowed font-bold text-slate-300",
                        ].join(" ")}
                        aria-pressed={active}
                        data-testid={`ads-entity-level-${item.id}`}
                    >
                        {item.label}
                        {active && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-amber-400" />}
                    </button>
                );
            })}
            <span className="mr-auto pb-3 text-[11px] font-bold text-slate-400">
                {platformLabel || "المنصة"} · قراءة فقط
            </span>
        </div>
    );
}

export default function AdsEntityLevelWorkspace({
    platform,
    platformLabel,
    entityLevel,
    onEntityLevelChange,
    campaigns,
    campaignTotals,
    campaignPagination,
    campaignPage,
    onCampaignPageChange,
    readOnly,
    adSquadReport,
    adSquadPage,
    onAdSquadPageChange,
    adSquadLoading,
    adSquadError,
}) {
    const adSquadsEnabled = platform === "snapchat";
    const adsEnabled = platform === "snapchat";
    return (
        <section data-testid="ads-entity-level-workspace">
            <style>{`
                [data-testid="ads-entity-level-workspace"] [data-testid="campaign-manager-table"] > div:first-child {
                    display: none;
                }
                [data-testid="ads-entity-level-workspace"] [data-testid="campaign-manager-table"] {
                    border-top-left-radius: 0;
                    border-top-right-radius: 0;
                }
            `}</style>
            <EntityTabs
                platformLabel={platformLabel}
                entityLevel={entityLevel}
                onChange={onEntityLevelChange}
                adSquadsEnabled={adSquadsEnabled}
                adsEnabled={adsEnabled}
            />
            {entityLevel === "campaigns" && (
                <CampaignManagerTable
                    platform={platform}
                    platformLabel={platformLabel}
                    campaigns={campaigns}
                    totals={campaignTotals}
                    pagination={campaignPagination}
                    page={campaignPage}
                    onPageChange={onCampaignPageChange}
                    readOnly={readOnly}
                />
            )}
            {entityLevel === "ad_squads" && (
                <>
                    <AdSquadSortControls />
                    <AdSquadManagerTable
                        rows={adSquadReport?.ad_squads || []}
                        totals={adSquadReport?.totals || {}}
                        pagination={adSquadReport?.pagination || {}}
                        page={adSquadPage}
                        onPageChange={onAdSquadPageChange}
                        loading={adSquadLoading}
                        error={adSquadError}
                    />
                </>
            )}
            {entityLevel === "ads" && <AdManagerTable />}
        </section>
    );
}
