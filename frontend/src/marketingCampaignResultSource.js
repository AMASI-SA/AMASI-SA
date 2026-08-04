import api from "./lib/api";

export const CAMPAIGN_RESULTS_SOURCE_EVENT = "mezan:campaign-results-source-updated";
export const CAMPAIGN_REPORT_UPDATED_EVENT = "mezan:campaign-report-updated";
export const CAMPAIGN_ACCOUNT_EVENT = "mezan:snapchat-campaign-account-updated";
export const CAMPAIGN_RESULTS_SOURCES = Object.freeze(["salla", "platform"]);

const STORAGE_PREFIX = "mezan-marketing-results-source-v1:";
const SNAPCHAT_ACCOUNT_STORAGE = "mezan-snapchat-manager-account-v1";
const SNAPCHAT_ACCOUNTS_STORAGE = "mezan-snapchat-manager-accounts-v1";
const snapshots = new Map();
let forceAccountToday = true;
let manualRangeSelected = false;
let campaignRequestSequence = 0;
let latestCampaignResponseSequence = 0;

function pathOnly(config = {}) {
  const raw = String(config?.url || "");
  return raw.split("?", 1)[0].replace(/\/+$/, "");
}

function isSnapchatCampaignReport(config = {}) {
  return pathOnly(config) === "/integrations-v2/snapchat_ads/campaign-report";
}

function safeSource(value) {
  return CAMPAIGN_RESULTS_SOURCES.includes(value) ? value : "salla";
}

function safeAccount(value = {}) {
  if (!value || typeof value !== "object") return null;
  const accountId = String(value.account_id || value.ad_account_id || "").trim();
  const timezone = String(value.timezone || value.account_timezone || "").trim();
  if (!accountId || !timezone) return null;
  const localToday = /^\d{4}-\d{2}-\d{2}$/.test(String(value.local_today || ""))
    ? String(value.local_today)
    : null;
  return {
    account_id: accountId,
    account_name: String(value.account_name || value.display_name || accountId).trim() || accountId,
    currency: String(value.currency || "").trim().toUpperCase() || null,
    timezone,
    local_today: localToday,
  };
}

export function applyEffectiveCampaignDelivery(payload) {
  if (!payload || typeof payload !== "object") return payload;
  if (!Array.isArray(payload.campaigns)) return payload;
  payload.campaigns.forEach((campaign) => {
    if (!campaign || typeof campaign !== "object") return;

    const configured = String(
      campaign.configured_status || campaign.status || "unknown",
    ).trim();
    campaign.configured_status = configured;
    campaign.status = configured;

    const deliveryLabel = String(
      campaign.delivery_label
      || campaign.effective_delivery_label
      || campaign.delivery_status
      || "",
    ).trim();
    if (deliveryLabel) campaign.delivery_status = deliveryLabel;

    if (!campaign.delivery_state) {
      const code = String(
        campaign.effective_delivery_code
        || campaign.delivery_reason_code
        || "",
      ).toUpperCase();
      if (code && code !== "DELIVERING") {
        campaign.delivery_state = code === "PENDING" ? "PENDING" : "NOT_DELIVERING";
      }
    }
  });
  return payload;
}

function readStoredAccounts() {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SNAPCHAT_ACCOUNTS_STORAGE) || "[]");
    return Array.isArray(parsed) ? parsed.map(safeAccount).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function writeStoredAccounts(accounts) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SNAPCHAT_ACCOUNTS_STORAGE, JSON.stringify(accounts));
  } catch {
    // The current response remains available through the in-memory snapshot.
  }
}

function writeSelectedAccount(accountId) {
  if (typeof window === "undefined") return;
  try {
    if (accountId) window.localStorage.setItem(SNAPCHAT_ACCOUNT_STORAGE, accountId);
    else window.localStorage.removeItem(SNAPCHAT_ACCOUNT_STORAGE);
  } catch {
    // The active request can still carry the selected account in memory/DOM.
  }
}

function clearStoredSnapchatAccounts() {
  writeSelectedAccount("");
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(SNAPCHAT_ACCOUNTS_STORAGE);
    } catch {
      // Retry can still proceed without mutating storage.
    }
  }
}

export function campaignResultsSource(platform = "snapchat") {
  if (typeof window === "undefined") return "salla";
  try {
    return safeSource(window.localStorage.getItem(`${STORAGE_PREFIX}${platform}`));
  } catch {
    return "salla";
  }
}

export function setCampaignResultsSource(source, platform = "snapchat") {
  const normalized = safeSource(source);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(`${STORAGE_PREFIX}${platform}`, normalized);
    } catch {
      // The active page still updates even if storage is blocked.
    }
    window.dispatchEvent(new CustomEvent(CAMPAIGN_RESULTS_SOURCE_EVENT, {
      detail: { platform, source: normalized },
    }));
  }
  return normalized;
}

export function snapchatAvailableAccounts() {
  const snapshotAccounts = getCampaignReportSnapshot("snapchat")?.available_accounts;
  if (Array.isArray(snapshotAccounts)) {
    const normalized = snapshotAccounts.map(safeAccount).filter(Boolean);
    if (normalized.length) return normalized;
  }
  return readStoredAccounts();
}

export function snapchatSelectedAccountId() {
  const snapshotId = String(
    getCampaignReportSnapshot("snapchat")?.selected_account_id || "",
  ).trim();
  if (snapshotId) return snapshotId;
  if (typeof window === "undefined") return "";
  try {
    return String(window.localStorage.getItem(SNAPCHAT_ACCOUNT_STORAGE) || "").trim();
  } catch {
    return "";
  }
}

export function snapchatSelectedAccount() {
  const selectedId = snapchatSelectedAccountId();
  return snapchatAvailableAccounts().find((account) => account.account_id === selectedId) || null;
}

export function setSnapchatSelectedAccount(accountId) {
  const normalized = String(accountId || "").trim();
  writeSelectedAccount(normalized);
  forceAccountToday = true;
  manualRangeSelected = false;
  clearCampaignReportSnapshot("snapchat");
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(CAMPAIGN_ACCOUNT_EVENT, {
      detail: { platform: "snapchat", account_id: normalized },
    }));
  }
  return normalized;
}

export function prepareSnapchatAccountPage() {
  forceAccountToday = true;
  manualRangeSelected = false;
}

export function markSnapchatManualRange() {
  manualRangeSelected = true;
  forceAccountToday = false;
}

export function snapchatManualRangeIsSelected() {
  return manualRangeSelected;
}

export function getCampaignReportSnapshot(platform = "snapchat") {
  return snapshots.get(platform) || null;
}

export function clearCampaignReportSnapshot(platform = "snapchat") {
  snapshots.delete(platform);
}

api.interceptors.request.use((config) => {
  if (!isSnapchatCampaignReport(config)) return config;
  const params = {
    ...(config.params || {}),
    result_source: campaignResultsSource("snapchat"),
  };
  const selectedAccountId = snapchatSelectedAccountId();
  if (selectedAccountId) params.account_id = selectedAccountId;

  if (forceAccountToday && !manualRangeSelected) {
    delete params.from_date;
    delete params.to_date;
    forceAccountToday = false;
  }

  const requestSequence = ++campaignRequestSequence;
  return {
    ...config,
    params,
    _mezanCampaignResultSource: true,
    _mezanSnapchatAccountTimezone: true,
    _mezanCampaignRequestSequence: requestSequence,
  };
});

api.interceptors.response.use((response) => {
  if (!isSnapchatCampaignReport(response?.config)) return response;
  const responseSequence = Number(response?.config?._mezanCampaignRequestSequence || 0);
  if (responseSequence > 0 && responseSequence < latestCampaignResponseSequence) {
    return response;
  }
  if (responseSequence > 0) latestCampaignResponseSequence = responseSequence;

  const payload = response?.data?.data && typeof response.data.data === "object"
    ? response.data.data
    : response.data;
  if (payload && typeof payload === "object") {
    applyEffectiveCampaignDelivery(payload);
    const accounts = Array.isArray(payload.available_accounts)
      ? payload.available_accounts.map(safeAccount).filter(Boolean)
      : [];
    if (accounts.length) writeStoredAccounts(accounts);
    const selectedId = String(
      payload.selected_account_id || payload.selected_account?.account_id || "",
    ).trim();
    if (selectedId) writeSelectedAccount(selectedId);
    snapshots.set("snapchat", payload);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(CAMPAIGN_REPORT_UPDATED_EVENT, {
        detail: {
          platform: "snapchat",
          source: safeSource(payload.result_source),
          account_id: selectedId || null,
          account_timezone: payload.account_timezone || null,
          request_sequence: responseSequence || null,
        },
      }));
    }
  }
  return response;
}, async (error) => {
  const config = error?.config;
  const code = error?.response?.data?.detail?.code;
  if (
    isSnapchatCampaignReport(config)
    && code === "snapchat_account_not_selected"
    && config?._mezanSnapchatAccountRetry !== true
  ) {
    clearStoredSnapchatAccounts();
    snapshots.delete("snapchat");
    forceAccountToday = true;
    manualRangeSelected = false;
    const retryConfig = {
      ...config,
      _mezanSnapchatAccountRetry: true,
      params: { ...(config.params || {}) },
    };
    delete retryConfig.params.account_id;
    delete retryConfig.params.from_date;
    delete retryConfig.params.to_date;
    return api.request(retryConfig);
  }
  return Promise.reject(error);
});
