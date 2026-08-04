import { getCampaignReportSnapshot } from "./marketingCampaignResultSource";

function text(value) {
  return String(value || "").trim();
}

function integer(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : fallback;
}

function keyFor(value = {}) {
  const accountId = text(value.account_id || value.ad_account_id);
  const campaignId = text(value.campaign_id || value.external_id);
  return accountId && campaignId ? `${accountId}:${campaignId}` : "";
}

function orderSemantics(raw = {}, normalized = {}) {
  const created = integer(raw.created_orders, integer(raw.orders, integer(normalized.orders)));
  const financial = integer(raw.financial_orders, created);
  const cancelled = integer(raw.cancelled_orders);
  const excluded = integer(raw.excluded_orders, Math.max(created - financial, 0));
  return {
    orders: created,
    created_orders: created,
    financial_orders: financial,
    cancelled_orders: cancelled,
    excluded_orders: excluded,
    other_excluded_orders: integer(raw.other_excluded_orders),
    order_count_source: text(raw.order_count_source) || "salla_created_orders_all_statuses",
  };
}

export function hydrateCampaignProfitability(
  campaigns = [],
  totals = {},
  snapshot = getCampaignReportSnapshot("snapchat"),
) {
  const rawCampaigns = Array.isArray(snapshot?.campaigns)
    ? snapshot.campaigns
    : [];
  const rawByCampaign = new Map();

  rawCampaigns.forEach((campaign) => {
    const key = keyFor(campaign);
    if (key) rawByCampaign.set(key, campaign);
  });

  const hydratedCampaigns = (Array.isArray(campaigns) ? campaigns : []).map((campaign) => {
    const raw = rawByCampaign.get(keyFor(campaign));
    if (!raw) return campaign;
    return {
      ...campaign,
      ...orderSemantics(raw, campaign),
      profitability: raw?.profitability && typeof raw.profitability === "object"
        ? raw.profitability
        : campaign.profitability,
    };
  });

  const rawTotals = snapshot?.totals && typeof snapshot.totals === "object"
    ? snapshot.totals
    : null;
  const hydratedTotals = rawTotals
    ? {
      ...(totals || {}),
      ...orderSemantics(rawTotals, totals || {}),
      profitability: rawTotals.profitability && typeof rawTotals.profitability === "object"
        ? rawTotals.profitability
        : totals?.profitability,
    }
    : (totals || {});

  return {
    campaigns: hydratedCampaigns,
    totals: hydratedTotals,
    hydrated_campaigns: hydratedCampaigns.filter(
      (campaign) => campaign?.profitability && typeof campaign.profitability === "object",
    ).length,
    order_semantics_hydrated_campaigns: hydratedCampaigns.filter(
      (campaign) => Number.isFinite(Number(campaign?.created_orders)),
    ).length,
    source: "snapchat_raw_report_snapshot",
  };
}

export const CAMPAIGN_PROFITABILITY_HYDRATION_POLICY = Object.freeze({
  exact_account_campaign_key: true,
  preserves_normalized_campaign_metrics: true,
  hydrates_created_orders_all_statuses: true,
  keeps_financial_orders_separate: true,
  reads_raw_snapshot_only: true,
  provider_writes_allowed: false,
});
