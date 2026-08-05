import api from "./lib/api";

export const ADS_REPORT_LOAD_STATE_EVENT = "mezan:ads-report-load-state";
export const ADS_DATE_RANGE_APPLIED_EVENT = "mezan:ads-date-range-applied";

const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const INDICATOR_ATTRIBUTE = "data-mezan-ads-report-load-indicator";
const IDENTITY_ATTRIBUTE = "data-mezan-sticky-identity-status";
const FOLDED_STATUS_ATTRIBUTE = "data-mezan-folded-status-cell";
const SALES_ATTRIBUTE = "data-mezan-sales-with-spend";
const FOLDED_SPEND_ATTRIBUTE = "data-mezan-folded-spend-cell";
const REQUEST_SEQUENCE_FIELD = "_mezanAdsTableUxRequestSequence";

const TABLE_CONFIGS = Object.freeze([
  {
    selector: '[data-testid="campaign-manager-table"] table',
    identityLabels: ["اسم الحملة"],
    salesLabels: ["مبيعات سلة"],
    identityWidth: 300,
    statusWidth: 120,
    stickyRight: 48,
    spendWidth: 145,
    salesWidth: 145,
  },
  {
    selector: '[data-testid="ad-squad-manager-table"] table',
    identityLabels: ["اسم المجموعة الإعلانية"],
    salesLabels: ["المبيعات"],
    identityWidth: 300,
    statusWidth: 120,
    stickyRight: 48,
    spendWidth: 145,
    salesWidth: 145,
  },
  {
    selector: '[data-testid="ad-manager-table"] table',
    identityLabels: ["الإعلان والإبداع"],
    salesLabels: ["المبيعات"],
    identityWidth: 330,
    statusWidth: 120,
    stickyRight: 0,
    spendWidth: 145,
    salesWidth: 145,
  },
]);

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

function normalizedHeader(value) {
  return clean(value).replace(/[↓↑]/g, "").trim();
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
  scheduleEnhancement();
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

function headerCells(table) {
  return [...(table.querySelector("thead tr")?.children || [])]
    .filter((cell) => ["TH", "TD"].includes(cell.tagName));
}

function columnIndex(headers, labels) {
  return headers.findIndex((cell) => {
    const text = normalizedHeader(cell.textContent);
    return labels.some((label) => text.startsWith(label));
  });
}

function rowCells(row) {
  return [...(row?.children || [])]
    .filter((cell) => ["TH", "TD"].includes(cell.tagName));
}

function tableRows(table) {
  return [
    ...table.querySelectorAll("thead tr"),
    ...table.querySelectorAll("tbody tr"),
    ...table.querySelectorAll("tfoot tr"),
  ];
}

function statusPresentation(statusCell, section) {
  if (section === "THEAD") return { label: "الحالة", active: false, header: true };
  if (section === "TFOOT") return { label: "", active: false, header: false };
  const label = clean(statusCell?.textContent) || "غير محسومة";
  const active = /^(نشط|نشطة)$/.test(label)
    || (/نشط/.test(label) && !/(غير|متوقف|متوقفة)/.test(label));
  return {
    label: active ? `✓ ${label}` : label,
    active,
    header: false,
  };
}

function foldStatusIntoIdentity(table, config, headers) {
  const identityIndex = columnIndex(headers, config.identityLabels);
  const statusIndex = columnIndex(headers, ["الحالة"]);
  if (identityIndex < 0 || statusIndex < 0 || identityIndex === statusIndex) return false;

  tableRows(table).forEach((row) => {
    const cells = rowCells(row);
    const identityCell = cells[identityIndex];
    const statusCell = cells[statusIndex];
    if (!identityCell || !statusCell) return;

    const section = row.parentElement?.tagName || "TBODY";
    const status = statusPresentation(statusCell, section);
    identityCell.setAttribute(IDENTITY_ATTRIBUTE, "true");
    identityCell.dataset.mezanStatusDisplay = status.label;
    identityCell.dataset.mezanStatusActive = status.active ? "true" : "false";
    identityCell.dataset.mezanStatusHeader = status.header ? "true" : "false";
    identityCell.style.setProperty("--mezan-identity-width", `${config.identityWidth}px`);
    identityCell.style.setProperty("--mezan-status-width", `${config.statusWidth}px`);
    identityCell.style.setProperty("--mezan-sticky-right", `${config.stickyRight}px`);
    identityCell.style.minWidth = `${config.identityWidth + config.statusWidth}px`;
    identityCell.style.width = `${config.identityWidth + config.statusWidth}px`;

    statusCell.setAttribute(FOLDED_STATUS_ATTRIBUTE, "true");
  });
  return true;
}

function installSpendSortBridge(salesCell, spendCell, spendWidth) {
  if (salesCell.dataset.mezanSpendSortBridge === "true") return;
  salesCell.dataset.mezanSpendSortBridge = "true";
  salesCell.addEventListener("click", (event) => {
    const rect = salesCell.getBoundingClientRect();
    const spendSegmentStart = rect.right - spendWidth;
    if (event.clientX < spendSegmentStart) return;
    const button = spendCell.querySelector("button");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    button.click();
  }, true);
}

function foldSpendBesideSales(table, config, headers) {
  const spendIndex = columnIndex(headers, ["المبلغ المصروف"]);
  const salesIndex = columnIndex(headers, config.salesLabels);
  if (spendIndex < 0 || salesIndex < 0) return false;

  // Already in the requested order: spend immediately before sales.
  if (spendIndex + 1 === salesIndex) return true;

  tableRows(table).forEach((row) => {
    const cells = rowCells(row);
    const spendCell = cells[spendIndex];
    const salesCell = cells[salesIndex];
    if (!spendCell || !salesCell) return;

    const section = row.parentElement?.tagName || "TBODY";
    const spendDisplay = section === "THEAD"
      ? "المبلغ المصروف"
      : section === "TFOOT"
        ? clean(spendCell.textContent)
        : clean(spendCell.textContent) || "—";

    salesCell.setAttribute(SALES_ATTRIBUTE, "true");
    salesCell.dataset.mezanSpendDisplay = spendDisplay;
    salesCell.dataset.mezanSpendHeader = section === "THEAD" ? "true" : "false";
    salesCell.style.setProperty("--mezan-spend-width", `${config.spendWidth}px`);
    salesCell.style.setProperty("--mezan-sales-width", `${config.salesWidth}px`);
    salesCell.style.minWidth = `${config.spendWidth + config.salesWidth}px`;
    salesCell.style.width = `${config.spendWidth + config.salesWidth}px`;

    spendCell.setAttribute(FOLDED_SPEND_ATTRIBUTE, "true");
    if (section === "THEAD") installSpendSortBridge(salesCell, spendCell, config.spendWidth);
  });
  return true;
}

export function enhanceMarketingAdsTables(root = document) {
  let enhanced = 0;
  TABLE_CONFIGS.forEach((config) => {
    root.querySelectorAll?.(config.selector).forEach((table) => {
      const headers = headerCells(table);
      if (!headers.length) return;
      const statusDone = foldStatusIntoIdentity(table, config, headers);
      const spendDone = foldSpendBesideSales(table, config, headers);
      if (statusDone || spendDone) enhanced += 1;
    });
  });
  return enhanced;
}

function enhanceAll() {
  renderAdsReportLoadIndicator(document);
  enhanceMarketingAdsTables(document);
}

function scheduleEnhancement() {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  if (frame) window.cancelAnimationFrame(frame);
  frame = window.requestAnimationFrame(() => {
    frame = 0;
    enhanceAll();
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
    setAdsReportLoadState("loading", {
      detail: dateDetail(config),
    });
    return {
      ...config,
      [REQUEST_SEQUENCE_FIELD]: sequence,
    };
  });

  api.interceptors.response.use((response) => {
    if (!isSnapchatCampaignReportRequest(response?.config)) return response;
    const sequence = Number(response?.config?.[REQUEST_SEQUENCE_FIELD] || 0);
    if (sequence && sequence < latestRequestSequence) return response;
    setAdsReportLoadState("success", {
      detail: dateDetail(response.config),
    });
    return response;
  }, (error) => {
    if (isSnapchatCampaignReportRequest(error?.config)) {
      const sequence = Number(error?.config?.[REQUEST_SEQUENCE_FIELD] || 0);
      if (!sequence || sequence >= latestRequestSequence) {
        setAdsReportLoadState("error", {
          detail: dateDetail(error.config),
        });
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
  window.addEventListener(ADS_REPORT_LOAD_STATE_EVENT, scheduleEnhancement);
  window.addEventListener("popstate", scheduleEnhancement);

  const observer = new MutationObserver(scheduleEnhancement);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  scheduleEnhancement();
  return true;
}

if (
  typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  installMarketingAdsTableUXEnhancer();
}
