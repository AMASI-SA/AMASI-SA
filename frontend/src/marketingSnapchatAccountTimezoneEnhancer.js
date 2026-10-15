import {
  CAMPAIGN_ACCOUNT_EVENT,
  CAMPAIGN_REPORT_UPDATED_EVENT,
  getCampaignReportSnapshot,
  markSnapchatManualRange,
  prepareSnapchatAccountPage,
  setSnapchatSelectedAccount,
  snapchatAvailableAccounts,
  snapchatManualRangeIsSelected,
  snapchatRestoredReturnRange,
  snapchatSelectedAccountId,
} from "./marketingCampaignResultSource";

const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const SWITCHER_ATTRIBUTE = "data-mezan-snapchat-account-switcher";
const COVERAGE_ATTRIBUTE = "data-mezan-snapchat-campaign-coverage";
const COVERAGE_TEXT = "إجمالي الحساب متوفر، لكن تفاصيل الحملات لنفس توقيت الحساب لم تكتمل بعد. ستُستكمل تلقائيًا في دورة المزامنة التالية.";
const preparedWorkspaces = new WeakSet();

function isSnapchatPage() {
  try {
    const pathname = String(window.location.pathname || "").replace(/\/+$/, "") || "/";
    if (pathname !== "/ads-manager") return false;
    const params = new URLSearchParams(window.location.search || "");
    return String(params.get("provider") || "").toLowerCase() === "snapchat";
  } catch {
    return false;
  }
}

function reportForm(workspace) {
  return workspace.querySelector("form");
}

function reportDateInputs(workspace) {
  return [...(reportForm(workspace)?.querySelectorAll('input[type="date"]') || [])].slice(0, 2);
}

function setReactInputValue(input, value) {
  if (!input || input.value === value) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  if (setter) setter.call(input, value);
  else input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function syncDateInputs(workspace, dateFrom, dateTo, { submit = false } = {}) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(dateFrom || ""))) return false;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(dateTo || ""))) return false;
  const [fromInput, toInput] = reportDateInputs(workspace);
  if (!fromInput || !toInput) return false;
  const fromChanged = setReactInputValue(fromInput, dateFrom);
  const toChanged = setReactInputValue(toInput, dateTo);
  const changed = fromChanged || toChanged;
  if (changed && submit) {
    const form = reportForm(workspace);
    if (form && form.dataset.mezanAccountDateSubmitPending !== "true") {
      form.dataset.mezanAccountDateSubmitPending = "true";
      requestAnimationFrame(() => {
        delete form.dataset.mezanAccountDateSubmitPending;
        if (typeof form.requestSubmit === "function") form.requestSubmit();
      });
    }
  }
  return changed;
}

function accountButton(account, selectedId, workspace) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "mezan-snapchat-account-card";
  button.dataset.accountId = account.account_id;
  const active = account.account_id === selectedId;
  button.dataset.active = active ? "true" : "false";
  button.setAttribute("aria-pressed", active ? "true" : "false");

  const header = document.createElement("span");
  header.className = "mezan-snapchat-account-card__header";
  const name = document.createElement("strong");
  name.textContent = account.account_name || account.account_id;
  const currency = document.createElement("span");
  currency.textContent = account.currency || "—";
  header.append(name, currency);

  const timezone = document.createElement("span");
  timezone.className = "mezan-snapchat-account-card__timezone";
  timezone.textContent = account.timezone;

  const today = document.createElement("span");
  today.className = "mezan-snapchat-account-card__today";
  today.textContent = account.local_today
    ? `اليوم في الحساب: ${account.local_today}`
    : "تاريخ الحساب غير متاح";

  button.append(header, timezone, today);
  button.addEventListener("click", () => {
    if (!account.account_id) return;
    setSnapchatSelectedAccount(account.account_id);
    if (account.local_today) {
      syncDateInputs(workspace, account.local_today, account.local_today);
    }
    const form = reportForm(workspace);
    if (form && typeof form.requestSubmit === "function") form.requestSubmit();
  });
  return button;
}

function accountSwitcherSignature(accounts, selectedId) {
  return JSON.stringify({
    selectedId,
    accounts: accounts.map((account) => ({
      account_id: account.account_id,
      account_name: account.account_name,
      currency: account.currency,
      timezone: account.timezone,
      local_today: account.local_today,
    })),
  });
}

function updateWorkspaceAccountMetadata(workspace, snapshot, accounts, selectedId) {
  workspace.dataset.snapchatSelectedAccount = selectedId;
  workspace.dataset.snapchatAccountTimezone = String(
    snapshot?.account_timezone
      || accounts.find((account) => account.account_id === selectedId)?.timezone
      || "",
  );
}

function ensureAccountSwitcher(workspace, snapshot) {
  const accounts = snapchatAvailableAccounts();
  if (!accounts.length) return;
  const selectedId = String(
    snapshot?.selected_account_id || snapchatSelectedAccountId() || accounts[0].account_id,
  );
  const signature = accountSwitcherSignature(accounts, selectedId);
  let switcher = workspace.querySelector(`[${SWITCHER_ATTRIBUTE}]`);
  if (switcher?.dataset.renderSignature === signature) {
    updateWorkspaceAccountMetadata(workspace, snapshot, accounts, selectedId);
    return;
  }
  if (!switcher) {
    switcher = document.createElement("section");
    switcher.setAttribute(SWITCHER_ATTRIBUTE, "true");
    switcher.className = "mezan-snapchat-account-switcher";
    const header = workspace.querySelector("header");
    if (header?.nextSibling) header.parentNode.insertBefore(switcher, header.nextSibling);
    else if (header) header.insertAdjacentElement("afterend", switcher);
    else workspace.prepend(switcher);
  }

  switcher.replaceChildren();
  const titleRow = document.createElement("div");
  titleRow.className = "mezan-snapchat-account-switcher__title";
  const title = document.createElement("div");
  title.innerHTML = "<strong>حساب Snapchat المعروض</strong><span>كل حساب بواجهته وتاريخه وتوقيته الأصلي</span>";
  const policy = document.createElement("span");
  policy.className = "mezan-snapchat-account-switcher__policy";
  policy.textContent = "لوحة التحكم والمحاسبة تبقيان بتوقيت الرياض 24 ساعة";
  titleRow.append(title, policy);

  const cards = document.createElement("div");
  cards.className = "mezan-snapchat-account-switcher__cards";
  accounts.forEach((account) => cards.appendChild(
    accountButton(account, selectedId, workspace),
  ));
  switcher.append(titleRow, cards);
  switcher.dataset.renderSignature = signature;
  updateWorkspaceAccountMetadata(workspace, snapshot, accounts, selectedId);
}

function ensureFormTracking(workspace) {
  const form = reportForm(workspace);
  if (!form || form.dataset.mezanSnapchatRangeTracking === "true") return;
  form.dataset.mezanSnapchatRangeTracking = "true";
  form.addEventListener("submit", () => markSnapchatManualRange());
}

function syncRestoredReturnRange(workspace) {
  const restored = snapchatRestoredReturnRange();
  if (!restored) return false;
  const [fromInput, toInput] = reportDateInputs(workspace);
  if (!fromInput || !toInput) return true;
  syncDateInputs(workspace, restored.date_from, restored.date_to, { submit: false });
  return true;
}

function syncReportedRange(workspace, snapshot) {
  if (syncRestoredReturnRange(workspace)) return;
  if (snapchatManualRangeIsSelected()) return;
  const dateFrom = String(snapshot?.date_from || "");
  const dateTo = String(snapshot?.date_to || "");
  if (!dateFrom || !dateTo) return;
  // Only the initial account-local-day response may initialize the form.
  // Once the user applies a range, React owns that range and no later or stale
  // response is allowed to overwrite it back to one day.
  syncDateInputs(workspace, dateFrom, dateTo, { submit: true });
}

function ensureCampaignCoverageMessage(workspace, snapshot) {
  const source = snapshot?.source || {};
  const spend = Number(snapshot?.totals?.spend_sar || 0);
  const missingCampaignRows = Number(source.account_rows || 0) > 0
    && Number(source.campaign_rows || 0) === 0
    && spend > 0;
  let notice = workspace.querySelector(`[${COVERAGE_ATTRIBUTE}]`);
  const table = workspace.querySelector('[data-testid="campaign-manager-table"]');
  if (!missingCampaignRows) {
    notice?.remove();
    return;
  }
  if (!notice) {
    notice = document.createElement("div");
    notice.setAttribute(COVERAGE_ATTRIBUTE, "true");
    notice.className = "mezan-snapchat-campaign-coverage";
  }
  if (notice.textContent !== COVERAGE_TEXT) notice.textContent = COVERAGE_TEXT;
  if (table && notice.nextElementSibling !== table) table.parentNode.insertBefore(notice, table);
  else if (!notice.isConnected) workspace.appendChild(notice);

  if (table) {
    [...table.querySelectorAll("div")].forEach((node) => {
      if (String(node.textContent || "").trim() === "لا توجد حملات موثقة ضمن الفترة أو البحث.") {
        node.textContent = "تفاصيل الحملات قيد الاستكمال لنفس توقيت الحساب.";
      }
    });
  }
}

export function enhanceSnapchatAccountTimezone(root = document) {
  if (!isSnapchatPage()) return false;
  const workspace = root.querySelector(WORKSPACE_SELECTOR);
  if (!workspace) return false;
  if (!preparedWorkspaces.has(workspace)) {
    preparedWorkspaces.add(workspace);
    prepareSnapchatAccountPage();
  }
  const snapshot = getCampaignReportSnapshot("snapchat");
  ensureFormTracking(workspace);
  ensureAccountSwitcher(workspace, snapshot);
  if (snapshot) {
    syncReportedRange(workspace, snapshot);
    ensureCampaignCoverageMessage(workspace, snapshot);
  } else {
    syncRestoredReturnRange(workspace);
  }
  return true;
}

let frame = 0;
function scheduleEnhancement() {
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = 0;
    enhanceSnapchatAccountTimezone(document);
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
  window.addEventListener(CAMPAIGN_ACCOUNT_EVENT, scheduleEnhancement);
  scheduleEnhancement();
}
