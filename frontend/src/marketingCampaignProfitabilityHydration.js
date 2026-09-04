export function hydrateCampaignProfitability(
  campaigns = [],
  totals = {},
  _snapshot = null,
) {
  // SNAP-REPORT-1 makes each HTTP response a self-contained generation.
  // Rehydrating from an in-memory response cache can combine account, date,
  // pagination, or source generations, so this compatibility helper is a
  // deliberate no-op.
  return {
    campaigns: Array.isArray(campaigns) ? campaigns : [],
    totals: totals || {},
    hydrated_campaigns: 0,
    order_semantics_hydrated_campaigns: 0,
    source: "disabled_for_source_coherence",
  };
}

export const CAMPAIGN_PROFITABILITY_HYDRATION_POLICY = Object.freeze({
  enabled: false,
  response_generation_is_self_contained: true,
  reads_raw_snapshot: false,
  provider_writes_allowed: false,
  accounting_writes_allowed: false,
});
