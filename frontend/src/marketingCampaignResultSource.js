import api from "./lib/api";

export const CAMPAIGN_RESULTS_SOURCE_EVENT = "mezan:campaign-results-source-updated";
export const CAMPAIGN_REPORT_UPDATED_EVENT = "mezan:campaign-report-updated";
export const CAMPAIGN_RESULTS_SOURCES = Object.freeze(["salla", "platform"]);

const STORAGE_PREFIX = "mezan-marketing-results-source-v1:";
const snapshots = new Map();

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

export function getCampaignReportSnapshot(platform = "snapchat") {
  return snapshots.get(platform) || null;
}

export function clearCampaignReportSnapshot(platform = "snapchat") {
  snapshots.delete(platform);
}

api.interceptors.request.use((config) => {
  if (!isSnapchatCampaignReport(config)) return config;
  return {
    ...config,
    params: {
      ...(config.params || {}),
      result_source: campaignResultsSource("snapchat"),
    },
    _mezanCampaignResultSource: true,
  };
});

api.interceptors.response.use((response) => {
  if (!isSnapchatCampaignReport(response?.config)) return response;
  const payload = response?.data?.data && typeof response.data.data === "object"
    ? response.data.data
    : response.data;
  if (payload && typeof payload === "object") {
    snapshots.set("snapchat", payload);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(CAMPAIGN_REPORT_UPDATED_EVENT, {
        detail: {
          platform: "snapchat",
          source: safeSource(payload.result_source),
        },
      }));
    }
  }
  return response;
});
