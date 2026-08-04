import { getCampaignReportSnapshot } from "./marketingCampaignResultSource";

function text(value) {
  return String(value || "").trim();
}

function keyFor(value = {}) {
  const accountId = text(value.account_id || value.ad_account_id);
  const campaignId = text(value.campaign_id || value.external_id);
  return accountId && campaignId ? `${accountId}:${campaignId}` : "";
}

export function hydrateCampaignProfitability(
  campaigns = [],
  totals = {},
  snapshot = getCampaignReportSnapshot("snapchat"),
) {
  const rawCampaigns = Array.isArray(snapshot?.campaigns)
    ? snapshot.campaigns
    : [];
  const profitabilityByCampaign = new Map();

  rawCampaigns.forEach((campaign) => {
    const key = keyFor(campaign);
    if (!key || !campaign?.profitability || typeof campaign.profitability !== "object") {
      return;
    }
    profitabilityByCampaign.set(key, campaign.profitability);
  });

  const hydratedCampaigns = (Array.isArray(campaigns) ? campaigns : []).map((campaign) => {
    const profitability = profitabilityByCampaign.get(keyFor(campaign));
    return profitability
      ? { ...campaign, profitability }
      : campaign;
  });

  const rawProfitabilityTotals = snapshot?.totals?.profitability;
  const hydratedTotals = rawProfitabilityTotals && typeof rawProfitabilityTotals === "object"
    ? { ...(totals || {}), profitability: rawProfitabilityTotals }
    : (totals || {});

  return {
    campaigns: hydratedCampaigns,
    totals: hydratedTotals,
    hydrated_campaigns: hydratedCampaigns.filter(
      (campaign) => campaign?.profitability && typeof campaign.profitability === "object",
    ).length,
    source: "snapchat_raw_report_snapshot",
  };
}

export const CAMPAIGN_PROFITABILITY_HYDRATION_POLICY = Object.freeze({
  exact_account_campaign_key: true,
  preserves_normalized_campaign_metrics: true,
  reads_raw_snapshot_only: true,
  provider_writes_allowed: false,
});
