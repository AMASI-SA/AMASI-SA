import {
  CAMPAIGN_REPORT_UPDATED_EVENT,
  CAMPAIGN_RESULTS_SOURCE_EVENT,
  campaignResultsSource,
  clearCampaignReportSnapshot,
  getCampaignReportSnapshot,
  setCampaignResultsSource,
} from "./marketingCampaignResultSource";

const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const TABLE_SELECTOR = '[data-testid="campaign-manager-table"]';
const ACTIONS_ATTRIBUTE = 'data-mezan-ads-manager-actions';
const SOURCE_TOGGLE_ATTRIBUTE = 'data-mezan-campaign-result-source';

const PLATFORM_KEYS = new Set(["snapchat", "meta", "tiktok", "google"]);

export function marketingPlatformFromLocation(locationLike = window.location) {
  try {
    const params = new URLSearchParams(locationLike.search || "");
    const provider = String(params.get("provider") || "").trim().toLowerCase();
    return PLATFORM_KEYS.has(provider) ? provider : "snapchat";
  } catch {
    return "snapchat";
  }
}

function lockedButton(label, className, title) {
  const button = document.createElement("button");
  button.type = "button";
  button.disabled = true;
  button.className = className;
  button.textContent = label;
  button.title = title;
  button.setAttribute("aria-disabled", "true");
  return button;
}

function ensureManagerActions(table, workspace) {
  const tabBar = table.firstElementChild;
  if (
    !tabBar
    || workspace?.querySelector('[data-testid="snapchat-campaign-management-panel"]')
    || tabBar.querySelector(`[${ACTIONS_ATTRIBUTE}]`)
  ) return;

  const actions = document.createElement("div");
  actions.setAttribute(ACTIONS_ATTRIBUTE, "true");
  actions.className = "mezan-ads-manager-primary-actions";
  actions.appendChild(lockedButton(
    "المسودات",
    "mezan-ads-manager-drafts",
    "المسودات ستُفعّل بعد اكتمال دورة الاعتماد والتنفيذ الآمن.",
  ));
  actions.appendChild(lockedButton(
    "+ إنشاء حملة",
    "mezan-ads-manager-create",
    "إنشاء الحملات مقفل حاليًا؛ الصفحة للقراءة والتحليل فقط.",
  ));
  tabBar.appendChild(actions);
}

function markEntityTabs(table) {
  const tabBar = table.firstElementChild;
  if (!tabBar) return;
  tabBar.setAttribute("data-mezan-ads-entity-tabs", "true");
  const buttons = [...tabBar.querySelectorAll("button")];
  buttons.slice(0, 3).forEach((button, index) => {
    button.setAttribute("data-mezan-entity-tab", ["campaigns", "adsets", "ads"][index]);
    if (button.disabled) {
      button.title = "سيُفعّل في المرحلة التالية بعد توفير مصدر البيانات الموثوق.";
    }
  });
}

function finiteCount(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : 0;
}

export function sourceDescription(source, snapshot) {
  if (source !== "salla") {
    return {
      text: "المشتريات والعائد كما أبلغت بها المنصة",
      status: "provider",
    };
  }
  const coverage = snapshot?.source?.salla_attribution;
  if (!coverage || typeof coverage !== "object") {
    return {
      text: "الطلبات والمبيعات الفعلية من سلة",
      status: "pending",
    };
  }
  // The created-order semantics layer is authoritative for the campaign
  // totals shown in the workspace. Keep the legacy field as a fallback for
  // older report payloads, but do not let its stale zero override the current
  // created-order match count.
  const matched = finiteCount(
    coverage.created_orders_matched ?? coverage.matched_orders,
  );
  const unmatched = finiteCount(coverage.unattributed_snapchat_orders);
  const ambiguous = finiteCount(coverage.ambiguous_orders);
  const parts = [`مطابقة ${matched.toLocaleString("en-US")} طلب من سلة بالحملات`];
  if (unmatched > 0) parts.push(`${unmatched.toLocaleString("en-US")} طلب سناب بلا حملة واضحة`);
  if (ambiguous > 0) parts.push(`${ambiguous.toLocaleString("en-US")} طلب مطابقته ملتبسة`);
  return {
    text: parts.join(" · "),
    status: unmatched > 0 || ambiguous > 0 ? "warning" : "complete",
  };
}

function updateSourceToggle(toggle, source, snapshot = null) {
  toggle.querySelectorAll("button[data-result-source]").forEach((button) => {
    const active = button.dataset.resultSource === source;
    button.dataset.active = active ? "true" : "false";
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  const state = sourceDescription(source, snapshot);
  toggle.dataset.coverageStatus = state.status;
  const description = toggle.querySelector("[data-result-source-description]");
  if (description && description.textContent !== state.text) {
    description.textContent = state.text;
  }
}

function ensureResultSourceToggle(workspace, platform) {
  if (platform !== "snapchat") return;
  const header = workspace.querySelector("header");
  if (!header) return;
  let toggle = header.querySelector(`[${SOURCE_TOGGLE_ATTRIBUTE}]`);
  if (!toggle) {
    toggle = document.createElement("div");
    toggle.setAttribute(SOURCE_TOGGLE_ATTRIBUTE, "true");
    toggle.className = "mezan-campaign-result-source";

    const label = document.createElement("div");
    label.className = "mezan-campaign-result-source__label";
    label.textContent = "مصدر النتائج";

    const controls = document.createElement("div");
    controls.className = "mezan-campaign-result-source__controls";

    const salla = document.createElement("button");
    salla.type = "button";
    salla.dataset.resultSource = "salla";
    salla.textContent = "سلة — النتائج الفعلية";

    const provider = document.createElement("button");
    provider.type = "button";
    provider.dataset.resultSource = "platform";
    provider.textContent = "الحساب الإعلاني";

    const description = document.createElement("div");
    description.className = "mezan-campaign-result-source__description";
    description.setAttribute("data-result-source-description", "true");

    controls.append(salla, provider);
    toggle.append(label, controls, description);

    toggle.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-result-source]");
      if (!button) return;
      const next = setCampaignResultsSource(button.dataset.resultSource, platform);
      clearCampaignReportSnapshot(platform);
      updateSourceToggle(toggle, next, null);
      workspace.dataset.campaignResultSource = next;
      const refresh = workspace.querySelector('[data-testid="marketing-platform-refresh"]');
      if (refresh && !refresh.disabled) refresh.click();
    });

    const statusBar = header.lastElementChild;
    if (statusBar && statusBar !== header.firstElementChild) {
      header.insertBefore(toggle, statusBar);
    } else {
      header.appendChild(toggle);
    }
  }
  const source = campaignResultsSource(platform);
  workspace.dataset.campaignResultSource = source;
  updateSourceToggle(toggle, source, getCampaignReportSnapshot(platform));
}

function headerIndex(table, matcher) {
  return [...table.querySelectorAll("thead th")].findIndex((cell) => matcher(
    String(cell.textContent || "").replace(/\s+/g, " ").trim(),
  ));
}

function moneyText(value, currency) {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  const amount = parsed.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return currency === "USD" ? `$${amount}` : `${amount} ر.س`;
}

function writeMetricCell(cell, value, currency, secondary = "") {
  if (!cell) return;
  const parsed = value === null || value === undefined || value === ""
    ? Number.NaN
    : Number(value);
  const normalizedValue = Number.isFinite(parsed) ? parsed : null;
  const signature = JSON.stringify([normalizedValue, currency, secondary]);
  if (cell.dataset.nativeCurrencySignature === signature) return;

  cell.replaceChildren();
  const wrapper = document.createElement("div");
  wrapper.className = "font-mono";
  const primary = document.createElement("div");
  primary.className = "font-extrabold text-slate-800";
  primary.textContent = moneyText(normalizedValue, currency);
  wrapper.appendChild(primary);
  if (secondary) {
    const note = document.createElement("div");
    note.className = "mt-0.5 text-[10px] font-semibold text-slate-400";
    note.textContent = secondary;
    wrapper.appendChild(note);
  }
  cell.appendChild(wrapper);
  cell.dataset.nativeCurrencyApplied = currency;
  cell.dataset.nativeCurrencySignature = signature;
}

function campaignIdForRow(row, campaigns) {
  const text = String(row.textContent || "");
  return campaigns.find((campaign) => {
    const id = String(campaign?.campaign_id || "").trim();
    return id && text.includes(id);
  })?.campaign_id || null;
}

function applyNativeCampaignCurrency(table, platform) {
  if (platform !== "snapchat") return;
  const snapshot = getCampaignReportSnapshot(platform);
  const campaigns = Array.isArray(snapshot?.campaigns) ? snapshot.campaigns : [];
  if (!campaigns.length) return;
  const byId = new Map(campaigns.map((campaign) => [String(campaign.campaign_id), campaign]));
  const indexes = {
    spend: headerIndex(table, (label) => label.includes("المبلغ المصروف")),
    cpa: headerIndex(table, (label) => label.includes("تكلفة النتيجة")),
    cpc: headerIndex(table, (label) => label === "eCPC" || label.includes("eCPC")),
    cpm: headerIndex(table, (label) => label.includes("eCPM")),
    sales: headerIndex(table, (label) => label.includes("مبيعات الشراء")),
  };
  table.querySelectorAll("tbody tr").forEach((row) => {
    const campaignId = campaignIdForRow(row, campaigns);
    const campaign = campaignId ? byId.get(String(campaignId)) : null;
    if (!campaign) return;
    const cells = [...row.children];
    const currency = String(campaign.display_currency || campaign.budget?.currency || "SAR").toUpperCase();
    writeMetricCell(cells[indexes.spend], campaign.spend_native, currency);
    writeMetricCell(cells[indexes.cpa], campaign.cpa_native, currency, "لكل عملية شراء");
    writeMetricCell(cells[indexes.cpc], campaign.cpc_native, currency);
    writeMetricCell(cells[indexes.cpm], campaign.cpm_native, currency);
    writeMetricCell(cells[indexes.sales], campaign.sales_native, currency);
  });
}

export function enhanceMarketingAdsManager(root = document) {
  const workspace = root.querySelector(WORKSPACE_SELECTOR);
  if (!workspace) return false;

  const platform = marketingPlatformFromLocation(window.location);
  workspace.dataset.marketingPlatform = platform;
  ensureResultSourceToggle(workspace, platform);

  const table = workspace.querySelector(TABLE_SELECTOR);
  if (!table) {
    delete workspace.dataset.adsManagerMode;
    return false;
  }

  workspace.dataset.adsManagerMode = "campaigns";
  table.dataset.adsManagerDesign = "v1";
  markEntityTabs(table);
  ensureManagerActions(table, workspace);
  applyNativeCampaignCurrency(table, platform);
  return true;
}

let frame = 0;
function scheduleEnhancement() {
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = 0;
    enhanceMarketingAdsManager(document);
  });
}

const canAutoEnhance = typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test";

if (canAutoEnhance) {
  const observer = new MutationObserver(scheduleEnhancement);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("popstate", scheduleEnhancement);
  window.addEventListener(CAMPAIGN_REPORT_UPDATED_EVENT, scheduleEnhancement);
  window.addEventListener(CAMPAIGN_RESULTS_SOURCE_EVENT, scheduleEnhancement);
  scheduleEnhancement();
}
