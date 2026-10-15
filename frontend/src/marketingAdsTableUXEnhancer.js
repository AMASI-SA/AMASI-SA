import api from "./lib/api";

export const ADS_REPORT_LOAD_STATE_EVENT = "mezan:ads-report-load-state";
export const ADS_DATE_RANGE_APPLIED_EVENT = "mezan:ads-date-range-applied";

const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const INDICATOR_ATTRIBUTE = "data-mezan-ads-report-load-indicator";
const REQUEST_SEQUENCE_FIELD = "_mezanAdsTableUxRequestSequence";

let reportLoadState = Object.freeze({
  state: "idle",
  message: "",
  detail: "",
});
let requestSequence = 0;
let latestRequestSequence = 0;
let frame = 0;

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function pathOnly(config = {}) {
  return String(config?.url || "").split("?", 1)[0].replace(/\/+$/, "");
}

export function isSnapchatCampaignReportRequest(config = {}) {
  return pathOnly(config) === "/integrations-v2/snapchat_ads/campaign-report";
}

function isSnapchatAdsPage(locationLike = typeof window !== "undefined" ? window.location : null) {
  try {
    const pathname = String(locationLike?.pathname || "").replace(/\/+$/, "") || "/";
    if (pathname !== "/ads-manager") return false;
    const provider = new URLSearchParams(locationLike?.search || "").get("provider");
    return String(provider || "").toLowerCase() === "snapchat";
  } catch {
    return false;
  }
}

function dateDetail(config = {}) {
  const params = config.params || {};
  const from = clean(params.from_date);
  const to = clean(params.to_date);
  if (!from || !to) return "";
  return from === to ? from : `${from} — ${to}`;
}

export function getAdsReportLoadState() {
  return { ...reportLoadState };
}

export function setAdsReportLoadState(state, {
  message = "",
  detail = "",
} = {}) {
  const normalized = ["idle", "loading", "success", "error"].includes(state)
    ? state
    : "idle";
  const defaults = {
    idle: "",
    loading: "جاري تحميل الفترة المحددة…",
    success: "تم تحميل التقرير بنجاح",
    error: "تعذر تحميل التقرير",
  };
  reportLoadState = Object.freeze({
    state: normalized,
    message: clean(message) || defaults[normalized],
    detail: clean(detail),
  });
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(ADS_REPORT_LOAD_STATE_EVENT, {
      detail: getAdsReportLoadState(),
    }));
  }
  scheduleIndicator();
  return getAdsReportLoadState();
}

function indicatorIcon(state) {
  const icon = document.createElement("span");
  icon.setAttribute("aria-hidden", "true");
  if (state === "loading") {
    icon.className = "mezan-ads-report-spinner";
  } else if (state === "success") {
    icon.className = "mezan-ads-report-state-icon mezan-ads-report-state-icon--success";
    icon.textContent = "✓";
  } else if (state === "error") {
    icon.className = "mezan-ads-report-state-icon mezan-ads-report-state-icon--error";
    icon.textContent = "!";
  }
  return icon;
}

export function renderAdsReportLoadIndicator(root = document) {
  const workspace = root.querySelector?.(WORKSPACE_SELECTOR);
  const form = workspace?.querySelector("form");
  if (!form) return false;

  let indicator = form.querySelector(`[${INDICATOR_ATTRIBUTE}]`);
  if (!indicator) {
    indicator = document.createElement("div");
    indicator.setAttribute(INDICATOR_ATTRIBUTE, "true");
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");
    form.appendChild(indicator);
  }

  indicator.dataset.state = reportLoadState.state;
  indicator.hidden = reportLoadState.state === "idle";
  indicator.replaceChildren();
  if (indicator.hidden) return true;

  const icon = indicatorIcon(reportLoadState.state);
  const copy = document.createElement("span");
  copy.className = "mezan-ads-report-state-copy";
  const title = document.createElement("strong");
  title.textContent = reportLoadState.message;
  copy.appendChild(title);
  if (reportLoadState.detail) {
    const detail = document.createElement("small");
    detail.textContent = reportLoadState.detail;
    copy.appendChild(detail);
  }
  indicator.append(icon, copy);
  return true;
}

// Kept as a compatibility export. Native React tables own their columns now.
export function enhanceMarketingAdsTables() {
  return 0;
}

function scheduleIndicator() {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  if (frame) window.cancelAnimationFrame(frame);
  frame = window.requestAnimationFrame(() => {
    frame = 0;
    renderAdsReportLoadIndicator(document);
  });
}

export function installMarketingAdsTableUXEnhancer() {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  if (window.__mezanMarketingAdsTableUXEnhancerInstalled) return false;
  window.__mezanMarketingAdsTableUXEnhancerInstalled = true;

  api.interceptors.request.use((config) => {
    if (!isSnapchatCampaignReportRequest(config)) return config;
    const sequence = ++requestSequence;
    latestRequestSequence = sequence;
    setAdsReportLoadState("loading", { detail: dateDetail(config) });
    return { ...config, [REQUEST_SEQUENCE_FIELD]: sequence };
  });

  api.interceptors.response.use((response) => {
    if (!isSnapchatCampaignReportRequest(response?.config)) return response;
    const sequence = Number(response?.config?.[REQUEST_SEQUENCE_FIELD] || 0);
    if (sequence && sequence < latestRequestSequence) return response;
    setAdsReportLoadState("success", { detail: dateDetail(response.config) });
    return response;
  }, (error) => {
    if (isSnapchatCampaignReportRequest(error?.config)) {
      const sequence = Number(error?.config?.[REQUEST_SEQUENCE_FIELD] || 0);
      if (!sequence || sequence >= latestRequestSequence) {
        setAdsReportLoadState("error", { detail: dateDetail(error.config) });
      }
    }
    return Promise.reject(error);
  });

  window.addEventListener(ADS_DATE_RANGE_APPLIED_EVENT, (event) => {
    if (!isSnapchatAdsPage(window.location)) return;
    const from = clean(event?.detail?.dateFrom);
    const to = clean(event?.detail?.dateTo);
    setAdsReportLoadState("loading", {
      detail: from && to ? (from === to ? from : `${from} — ${to}`) : "",
    });
  });
  window.addEventListener(ADS_REPORT_LOAD_STATE_EVENT, scheduleIndicator);
  window.addEventListener("popstate", scheduleIndicator);

  const observer = new MutationObserver(scheduleIndicator);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  scheduleIndicator();
  return true;
}

export const MARKETING_ADS_TABLE_UX_POLICY = Object.freeze({
  load_indicator_enabled: true,
  table_dom_mutations_enabled: false,
  columns_rendered_by_react: true,
  provider_writes_allowed: false,
});

if (
  typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  installMarketingAdsTableUXEnhancer();
}
