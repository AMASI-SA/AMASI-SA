const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const TABLE_SELECTOR = '[data-testid="campaign-manager-table"]';
const ACTIONS_ATTRIBUTE = 'data-mezan-ads-manager-actions';

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

function ensureManagerActions(table) {
  const tabBar = table.firstElementChild;
  if (!tabBar || tabBar.querySelector(`[${ACTIONS_ATTRIBUTE}]`)) return;

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

export function enhanceMarketingAdsManager(root = document) {
  const workspace = root.querySelector(WORKSPACE_SELECTOR);
  if (!workspace) return false;

  workspace.dataset.marketingPlatform = marketingPlatformFromLocation(window.location);
  const table = workspace.querySelector(TABLE_SELECTOR);
  if (!table) {
    delete workspace.dataset.adsManagerMode;
    return false;
  }

  workspace.dataset.adsManagerMode = "campaigns";
  table.dataset.adsManagerDesign = "v1";
  markEntityTabs(table);
  ensureManagerActions(table);
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
  scheduleEnhancement();
}