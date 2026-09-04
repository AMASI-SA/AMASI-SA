import api from "./lib/api";
import {
  campaignResultsSource,
  getCampaignReportSnapshot,
} from "./marketingCampaignResultSource";

const CAMPAIGN_REPORT_PATH = "/integrations-v2/snapchat_ads/campaign-report";
const RETRY_FIELD = "_mezanSelectedSourceRetry";

function pathOnly(config = {}) {
  return String(config?.url || "").split("?", 1)[0].replace(/\/+$/, "");
}

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function whole(value, fallback = 0) {
  const parsed = finite(value);
  return parsed === null || parsed < 0 ? fallback : Math.trunc(parsed);
}

function ratio(numerator, denominator) {
  const top = finite(numerator);
  const bottom = finite(denominator);
  if (top === null || bottom === null || bottom <= 0) return null;
  return Math.round((top / bottom) * 1_000_000) / 1_000_000;
}

function responsePayload(response) {
  const data = response?.data;
  return data?.data && typeof data.data === "object" ? data.data : data;
}

export function isSnapchatCampaignReportResponse(response = {}) {
  return pathOnly(response?.config) === CAMPAIGN_REPORT_PATH;
}

export function shouldRetrySelectedCampaignSource(
  payload,
  selectedSource = campaignResultsSource("snapchat"),
  config = {},
) {
  return selectedSource === "salla"
    && String(payload?.result_source || "").toLowerCase() === "platform"
    && config?.[RETRY_FIELD] !== true;
}

function sallaCampaignMetrics(campaign = {}) {
  const salla = campaign?.salla_results && typeof campaign.salla_results === "object"
    ? campaign.salla_results
    : null;
  if (!salla) return null;

  const created = whole(
    salla.created_orders,
    whole(salla.orders, whole(campaign.created_orders, whole(campaign.orders))),
  );
  const financial = whole(salla.financial_orders, whole(campaign.financial_orders));
  const cancelled = whole(salla.cancelled_orders, whole(campaign.cancelled_orders));
  const excluded = whole(
    salla.excluded_orders,
    Math.max(created - financial, 0),
  );
  const sales = finite(salla.sales_sar);
  if (sales === null) return null;

  return {
    orders: created,
    created_orders: created,
    financial_orders: financial,
    cancelled_orders: cancelled,
    excluded_orders: excluded,
    other_excluded_orders: whole(
      salla.other_excluded_orders,
      whole(campaign.other_excluded_orders),
    ),
    sales_sar: Math.round(sales * 100) / 100,
    order_count_source: "salla_created_orders_all_statuses",
  };
}

function sallaTotalMetrics(totals = {}) {
  const created = whole(totals.created_orders, whole(totals.orders));
  const financial = whole(totals.financial_orders);
  const cancelled = whole(totals.cancelled_orders);
  const excluded = whole(totals.excluded_orders, Math.max(created - financial, 0));
  const profitSales = finite(totals?.profitability?.sales_sar);
  const sales = profitSales !== null
    ? profitSales
    : String(totals.result_source || "").toLowerCase() === "salla"
      ? finite(totals.sales_sar)
      : null;
  if (sales === null) return null;

  return {
    orders: created,
    created_orders: created,
    financial_orders: financial,
    cancelled_orders: cancelled,
    excluded_orders: excluded,
    sales_sar: Math.round(sales * 100) / 100,
    order_count_source: "salla_created_orders_all_statuses",
  };
}

function applyDerivedRatios(target, metrics) {
  const spend = finite(target?.spend_sar);
  return {
    ...metrics,
    cpa_sar: ratio(spend, metrics.orders),
    roas: ratio(metrics.sales_sar, spend),
    result_source: "salla",
  };
}

export function applySelectedSallaCampaignMetrics(payload) {
  if (!payload || typeof payload !== "object") return payload;

  const campaigns = Array.isArray(payload.campaigns) ? payload.campaigns : [];
  campaigns.forEach((campaign) => {
    if (!campaign || typeof campaign !== "object") return;
    const metrics = sallaCampaignMetrics(campaign);
    if (!metrics) return;
    Object.assign(campaign, applyDerivedRatios(campaign, metrics));
  });

  const totals = payload.totals && typeof payload.totals === "object"
    ? payload.totals
    : null;
  if (totals) {
    const metrics = sallaTotalMetrics(totals);
    if (metrics) Object.assign(totals, applyDerivedRatios(totals, metrics));
  }

  if (Array.isArray(payload.accounts) && payload.accounts[0] && totals) {
    Object.assign(payload.accounts[0], {
      orders: totals.orders,
      created_orders: totals.created_orders,
      financial_orders: totals.financial_orders,
      cancelled_orders: totals.cancelled_orders,
      excluded_orders: totals.excluded_orders,
      sales_sar: totals.sales_sar,
      cpa_sar: totals.cpa_sar,
      roas: totals.roas,
      result_source: "salla",
    });
  }

  payload.result_source = "salla";
  payload.selected_result_source = "salla";
  payload.source = payload.source && typeof payload.source === "object"
    ? payload.source
    : {};
  payload.source.selected_result_source_guard = {
    source: "salla",
    orders: "salla_results.created_orders",
    sales: "salla_results.sales_sar",
    spend: "snapchat",
    final_response_stage: true,
    snapshot_hydrated: true,
    read_only: true,
  };
  return payload;
}

export function finalizeSelectedCampaignSource(
  response,
  selectedSource = campaignResultsSource("snapchat"),
  snapshot = getCampaignReportSnapshot("snapchat"),
) {
  // Source-specific fields are finalized by the backend. Mutating a completed
  // response here could combine different account/range generations.
  return response;
}

api.interceptors.response.use(async (response) => {
  if (!isSnapchatCampaignReportResponse(response)) return response;

  const selectedSource = campaignResultsSource("snapchat");
  const payload = responsePayload(response);
  if (shouldRetrySelectedCampaignSource(payload, selectedSource, response.config)) {
    return api.request({
      ...response.config,
      [RETRY_FIELD]: true,
      params: {
        ...(response.config?.params || {}),
        result_source: "salla",
      },
    });
  }

  return finalizeSelectedCampaignSource(response, selectedSource);
});

export const SELECTED_SOURCE_GUARD_POLICY = Object.freeze({
  salla_orders_field: "salla_orders",
  salla_sales_field: "salla_sales_sar",
  spend_source: "snapchat_spend_sar",
  retries_stale_platform_response_once: true,
  runs_after_stale_response_guard: true,
  hydrates_raw_snapshot_after_final_response: false,
  provider_writes_allowed: false,
  accounting_writes_allowed: false,
});
