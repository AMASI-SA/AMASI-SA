import api from "./lib/api";
import {
  CAMPAIGN_ACCOUNT_EVENT,
  snapchatSelectedAccountId,
} from "./marketingCampaignResultSource";

const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const REFRESH_BUTTON_SELECTOR = '[data-testid="marketing-platform-refresh"]';
const CAMPAIGNS_TAB_SELECTOR = '[data-testid="marketing-platform-tab-campaigns"]';
const ENTITY_TABS_SELECTOR = '[data-testid="ads-entity-level-tabs"]';
const AUTO_REFRESH_MS = 60_000;
const MIN_FOCUS_REFRESH_MS = 20_000;
const SORT_STORAGE_KEY = "mezan-snapchat-adsquad-sort-v1";
const SORT_EVENT = "mezan:snapchat-adsquad-sort-updated";
const SORT_CONTROL_ATTRIBUTE = "data-mezan-adsquad-sort-controls";
const VALID_SORTS = new Set(["newest", "spend", "active"]);
const ADSQUAD_SORTING_PAGE_SIZE = 100;

let lastRefreshAt = 0;
let timer = 0;
let frame = 0;

function pathOnly(config = {}) {
  return String(config?.url || "").split("?", 1)[0].replace(/\/+$/, "");
}

function isAdSquadReport(config = {}) {
  return pathOnly(config) === "/integrations-v2/snapchat_ads/ad-squad-report";
}

function isAdsManagerProviderPage() {
  const pathname = String(window.location.pathname || "").replace(/\/+$/, "") || "/";
  if (pathname !== "/ads-manager") return false;
  return Boolean(new URLSearchParams(window.location.search || "").get("provider"));
}

function isSnapchatPage() {
  if (!isAdsManagerProviderPage()) return false;
  return String(new URLSearchParams(window.location.search || "").get("provider") || "").toLowerCase() === "snapchat";
}

export function adsquadSortPreference(storage = typeof window !== "undefined" ? window.localStorage : null) {
  try {
    const value = String(storage?.getItem(SORT_STORAGE_KEY) || "newest");
    return VALID_SORTS.has(value) ? value : "newest";
  } catch {
    return "newest";
  }
}

export function setAdsquadSortPreference(value, storage = typeof window !== "undefined" ? window.localStorage : null) {
  const normalized = VALID_SORTS.has(String(value || "")) ? String(value) : "newest";
  try {
    storage?.setItem(SORT_STORAGE_KEY, normalized);
  } catch {
    // The current page can still refresh with the in-memory event value.
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(SORT_EVENT, { detail: { sort_by: normalized } }));
  }
  return normalized;
}

function clickRefresh({ force = false } = {}) {
  if (!isAdsManagerProviderPage() || document.visibilityState === "hidden") return false;
  const now = Date.now();
  if (!force && now - lastRefreshAt < MIN_FOCUS_REFRESH_MS) return false;
  const workspace = document.querySelector(WORKSPACE_SELECTOR);
  const button = workspace?.querySelector(REFRESH_BUTTON_SELECTOR);
  if (!button || button.disabled) return false;
  lastRefreshAt = now;
  button.click();
  return true;
}

function defaultToCampaigns(workspace) {
  const provider = String(new URLSearchParams(window.location.search || "").get("provider") || "");
  if (!provider) return;
  if (workspace.dataset.mezanDefaultCampaignsProvider === provider) return;
  const campaigns = workspace.querySelector(CAMPAIGNS_TAB_SELECTOR);
  if (!campaigns) return;
  workspace.dataset.mezanDefaultCampaignsProvider = provider;
  if (campaigns.getAttribute("aria-pressed") !== "true") campaigns.click();
}

function sortControlButton(id, label, current) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.sortBy = id;
  button.textContent = label;
  button.setAttribute("aria-pressed", current === id ? "true" : "false");
  button.className = [
    "rounded-lg px-3 py-2 text-xs font-black transition",
    current === id
      ? "bg-slate-950 text-white shadow-sm"
      : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
  ].join(" ");
  button.addEventListener("click", () => {
    const selected = setAdsquadSortPreference(id);
    updateSortControls(document, selected);
    clickRefresh({ force: true });
  });
  return button;
}

export function updateSortControls(root = document, forcedSort = null) {
  if (!isSnapchatPage()) return false;
  const workspace = root.querySelector(WORKSPACE_SELECTOR);
  const tabs = workspace?.querySelector(ENTITY_TABS_SELECTOR);
  if (!workspace || !tabs) return false;
  const current = forcedSort || adsquadSortPreference();
  let controls = workspace.querySelector(`[${SORT_CONTROL_ATTRIBUTE}]`);
  if (!controls) {
    controls = document.createElement("div");
    controls.setAttribute(SORT_CONTROL_ATTRIBUTE, "true");
    controls.className = "flex flex-wrap items-center justify-between gap-2 border-x border-t border-slate-200 bg-slate-50 px-3 py-2";
    tabs.insertAdjacentElement("afterend", controls);
  }
  if (controls.dataset.sortSignature === current) return true;
  controls.replaceChildren();
  const label = document.createElement("div");
  label.className = "text-xs font-black text-slate-600";
  label.textContent = "ترتيب المجموعات الإعلانية";
  const actions = document.createElement("div");
  actions.className = "flex flex-wrap gap-2";
  actions.append(
    sortControlButton("newest", "الأحدث أولًا", current),
    sortControlButton("spend", "الأكثر صرفًا", current),
    sortControlButton("active", "النشطة أولًا", current),
  );
  controls.append(label, actions);
  controls.dataset.sortSignature = current;
  return true;
}

export function enhanceAdsLiveWorkspace(root = document) {
  if (!isAdsManagerProviderPage()) return false;
  const workspace = root.querySelector(WORKSPACE_SELECTOR);
  if (!workspace) return false;
  defaultToCampaigns(workspace);
  updateSortControls(root);
  return true;
}

api.interceptors.request.use((config) => {
  if (!isAdSquadReport(config)) return config;
  const params = {
    ...(config.params || {}),
    sort_by: adsquadSortPreference(),
    limit: Math.max(Number(config.params?.limit || 0), ADSQUAD_SORTING_PAGE_SIZE),
  };
  const accountId = snapchatSelectedAccountId();
  if (accountId) params.account_id = accountId;
  return { ...config, params, _mezanSnapchatAdSquadLive: true };
});

function scheduleEnhance() {
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = 0;
    enhanceAdsLiveWorkspace(document);
  });
}

function startTimer() {
  if (timer) window.clearInterval(timer);
  timer = window.setInterval(() => clickRefresh(), AUTO_REFRESH_MS);
}

const canStart = typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test";

if (canStart) {
  const observer = new MutationObserver(scheduleEnhance);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("popstate", scheduleEnhance);
  window.addEventListener("focus", () => clickRefresh());
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") clickRefresh();
  });
  window.addEventListener(CAMPAIGN_ACCOUNT_EVENT, () => {
    scheduleEnhance();
    window.setTimeout(() => clickRefresh({ force: true }), 60);
  });
  window.addEventListener(SORT_EVENT, scheduleEnhance);
  startTimer();
  scheduleEnhance();
}

export const ADS_LIVE_WORKSPACE_POLICY = Object.freeze({
  default_tab: "campaigns",
  auto_refresh_ms: AUTO_REFRESH_MS,
  refresh_only_when_visible: true,
  adsquad_default_sort: "newest",
  adsquad_sorting_page_size: ADSQUAD_SORTING_PAGE_SIZE,
  provider_mutations_allowed: false,
});
