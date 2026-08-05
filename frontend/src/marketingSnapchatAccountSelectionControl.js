import {
  getSnapchatAccountSelection,
  saveSnapchatAccountSelection,
} from "./services/snapchatIntegrationsV2";

const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const REFRESH_SELECTOR = '[data-testid="marketing-platform-refresh"]';
const CONTROL_ATTRIBUTE = "data-mezan-snapchat-account-selection-control";
const LOCATION_KEY_ATTRIBUTE = "data-mezan-snapchat-account-selection-location";
const SAVED_EVENT = "mezan:snapchat-account-selection-saved";

let installed = false;
let animationFrame = 0;
let loadSequence = 0;
let currentLocationKey = "";
let viewState = initialViewState();

function initialViewState() {
  return {
    selection: null,
    draftIds: new Set(),
    loading: false,
    saving: false,
    error: "",
    success: "",
  };
}

function clean(value) {
  return String(value || "").trim();
}

export function isSnapchatAdsManagerLocation(locationLike = window.location) {
  try {
    const pathname = clean(locationLike?.pathname).replace(/\/+$/, "") || "/";
    if (pathname !== "/ads-manager") return false;
    const provider = new URLSearchParams(locationLike?.search || "").get("provider");
    return clean(provider).toLowerCase() === "snapchat";
  } catch {
    return false;
  }
}

export function selectionDraftFromResponse(selection = {}) {
  return new Set(
    (Array.isArray(selection?.accounts) ? selection.accounts : [])
      .filter((account) => account?.selected === true)
      .map((account) => clean(account?.account_id))
      .filter(Boolean),
  );
}

export function toggleSelectionDraft(draftIds, accountId, checked) {
  const next = new Set(draftIds instanceof Set ? draftIds : []);
  const id = clean(accountId);
  if (!id) return next;
  if (checked) next.add(id);
  else next.delete(id);
  return next;
}

export function accountSelectionErrorMessage(error) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail?.message) return clean(detail.message);
  if (error?.code === "snapchat_account_selection_required") {
    return "اختر حساب Snapchat واحدًا على الأقل قبل الحفظ.";
  }
  return "تعذر تحميل أو حفظ حسابات Snapchat الآن. أعد المحاولة دون إعادة ربط التطبيق.";
}

function locationKey(locationLike = window.location) {
  return `${clean(locationLike?.pathname)}${clean(locationLike?.search)}`;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function accountTitle(account = {}) {
  return clean(account.display_name) || "حساب Snapchat";
}

function accountMetadata(account = {}) {
  return [clean(account.currency), clean(account.timezone)].filter(Boolean).join(" · ") || "بيانات العملة والتوقيت غير متاحة";
}

function hasSelectionChanged(selection, draftIds) {
  const saved = selectionDraftFromResponse(selection);
  if (saved.size !== draftIds.size) return true;
  return [...saved].some((id) => !draftIds.has(id));
}

export function renderSnapchatAccountSelectionControl(
  host,
  state,
  { onReload = () => {}, onToggle = () => {}, onSave = () => {} } = {},
) {
  if (!host) return null;
  host.replaceChildren();
  host.setAttribute(CONTROL_ATTRIBUTE, "true");

  const header = element("div", "mezan-snap-account-control__header");
  const heading = element("div");
  heading.appendChild(element("h2", "mezan-snap-account-control__title", "اختيار حسابات Snapchat للتقارير"));
  heading.appendChild(element(
    "p",
    "mezan-snap-account-control__description",
    "هذا الاختيار مستقل عن تقرير الحملات؛ لذلك يمكنك استعادة التشغيل حتى عندما يفشل التقرير بسبب عدم وجود حساب محدد.",
  ));
  header.appendChild(heading);

  const reload = element("button", "mezan-snap-account-control__reload", state.loading ? "جاري تحميل الحسابات…" : "إعادة تحميل الحسابات");
  reload.type = "button";
  reload.disabled = state.loading || state.saving;
  reload.addEventListener("click", onReload);
  header.appendChild(reload);
  host.appendChild(header);

  if (state.loading && !state.selection) {
    host.appendChild(element("div", "mezan-snap-account-control__notice", "جاري قراءة الحسابات المكتشفة من ربط Snapchat…"));
    return host;
  }

  const accounts = Array.isArray(state.selection?.accounts) ? state.selection.accounts : [];
  const summary = element("div", "mezan-snap-account-control__summary");
  summary.appendChild(element("span", "", `${accounts.length} حسابات مكتشفة`));
  summary.appendChild(element("span", "", `${state.draftIds.size} محدد للتقارير`));
  host.appendChild(summary);

  if (!accounts.length) {
    const empty = element("div", "mezan-snap-account-control__empty");
    empty.appendChild(element("strong", "", "لا توجد حسابات مكتشفة داخل ميزان."));
    empty.appendChild(element("span", "", "افتح إدارة ربط التطبيق وأعد توثيق Snapchat، ثم ارجع واضغط إعادة تحميل الحسابات."));
    const manage = element("a", "mezan-snap-account-control__manage", "إدارة ربط التطبيق");
    manage.href = "/integrations-v2?provider=snapchat_ads";
    empty.appendChild(manage);
    host.appendChild(empty);
  } else {
    const list = element("div", "mezan-snap-account-control__list");
    for (const account of accounts) {
      const id = clean(account.account_id);
      if (!id) continue;
      const label = element("label", "mezan-snap-account-control__account");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = id;
      checkbox.checked = state.draftIds.has(id);
      checkbox.disabled = state.saving;
      checkbox.setAttribute("data-mezan-snap-account-checkbox", id);
      checkbox.addEventListener("change", (event) => onToggle(id, event.target.checked));
      label.appendChild(checkbox);

      const details = element("span", "mezan-snap-account-control__account-details");
      details.appendChild(element("strong", "", accountTitle(account)));
      details.appendChild(element("span", "mezan-snap-account-control__account-id", id));
      details.appendChild(element("span", "mezan-snap-account-control__account-meta", accountMetadata(account)));
      label.appendChild(details);
      list.appendChild(label);
    }
    host.appendChild(list);
  }

  if (state.error) host.appendChild(element("div", "mezan-snap-account-control__message is-error", state.error));
  if (state.success) host.appendChild(element("div", "mezan-snap-account-control__message is-success", state.success));

  const footer = element("div", "mezan-snap-account-control__footer");
  const hint = element(
    "span",
    "mezan-snap-account-control__hint",
    state.draftIds.size
      ? "بعد الحفظ سيُعاد تحميل تقرير الحملات تلقائيًا دون تغيير الفترة المحددة."
      : "يجب تحديد حساب واحد على الأقل حتى يعمل التقرير والمزامنة.",
  );
  footer.appendChild(hint);

  const save = element(
    "button",
    "mezan-snap-account-control__save",
    state.saving
      ? "جاري حفظ الاختيار…"
      : hasSelectionChanged(state.selection, state.draftIds)
        ? "حفظ وتشغيل التقرير"
        : "تأكيد الاختيار وتشغيل التقرير",
  );
  save.type = "button";
  save.disabled = state.saving || state.loading || !accounts.length || state.draftIds.size < 1;
  save.addEventListener("click", onSave);
  footer.appendChild(save);
  host.appendChild(footer);
  return host;
}

function ensureHost() {
  if (!isSnapchatAdsManagerLocation(window.location)) return null;
  const workspace = document.querySelector(WORKSPACE_SELECTOR);
  if (!workspace) return null;
  let host = workspace.querySelector(`[${CONTROL_ATTRIBUTE}]`);
  if (!host) {
    host = document.createElement("section");
    host.setAttribute(CONTROL_ATTRIBUTE, "true");
    host.setAttribute(LOCATION_KEY_ATTRIBUTE, locationKey());
    const header = workspace.querySelector("header") || workspace.firstElementChild;
    if (header?.parentNode === workspace) header.insertAdjacentElement("afterend", host);
    else workspace.prepend(host);
  }
  return host;
}

function renderCurrent() {
  const host = ensureHost();
  if (!host) return;
  renderSnapchatAccountSelectionControl(host, viewState, {
    onReload: () => loadSelection(true),
    onToggle: (accountId, checked) => {
      viewState = {
        ...viewState,
        draftIds: toggleSelectionDraft(viewState.draftIds, accountId, checked),
        error: "",
        success: "",
      };
      renderCurrent();
    },
    onSave: saveSelection,
  });
}

async function loadSelection(force = false) {
  const host = ensureHost();
  if (!host) return null;
  if (viewState.loading && !force) return null;
  const sequence = ++loadSequence;
  viewState = { ...viewState, loading: true, error: "", success: "" };
  renderCurrent();
  try {
    const selection = await getSnapchatAccountSelection();
    if (sequence !== loadSequence) return null;
    viewState = {
      ...viewState,
      selection,
      draftIds: selectionDraftFromResponse(selection),
      loading: false,
      error: "",
    };
    renderCurrent();
    return selection;
  } catch (error) {
    if (sequence !== loadSequence) return null;
    viewState = {
      ...viewState,
      loading: false,
      error: accountSelectionErrorMessage(error),
    };
    renderCurrent();
    return null;
  }
}

async function saveSelection() {
  if (viewState.saving || viewState.draftIds.size < 1) return null;
  viewState = { ...viewState, saving: true, error: "", success: "" };
  renderCurrent();
  try {
    const selection = await saveSnapchatAccountSelection([...viewState.draftIds]);
    viewState = {
      ...viewState,
      selection,
      draftIds: selectionDraftFromResponse(selection),
      saving: false,
      success: "تم حفظ الحسابات المحددة. جارٍ إعادة تشغيل تقرير Snapchat…",
    };
    renderCurrent();
    window.dispatchEvent(new CustomEvent(SAVED_EVENT, {
      detail: {
        selectedAccountIds: [...viewState.draftIds],
        selectedCount: viewState.draftIds.size,
      },
    }));
    window.setTimeout(() => {
      document.querySelector(REFRESH_SELECTOR)?.click();
    }, 120);
    return selection;
  } catch (error) {
    viewState = {
      ...viewState,
      saving: false,
      error: accountSelectionErrorMessage(error),
    };
    renderCurrent();
    return null;
  }
}

function reconcile() {
  animationFrame = 0;
  if (!isSnapchatAdsManagerLocation(window.location)) {
    document.querySelector(`[${CONTROL_ATTRIBUTE}]`)?.remove();
    currentLocationKey = "";
    viewState = initialViewState();
    return;
  }
  const nextKey = locationKey();
  const host = ensureHost();
  if (!host) return;
  if (currentLocationKey !== nextKey) {
    currentLocationKey = nextKey;
    viewState = initialViewState();
    loadSelection();
    return;
  }
  renderCurrent();
  if (!viewState.selection && !viewState.loading && !viewState.error) loadSelection();
}

function scheduleReconcile() {
  if (animationFrame) cancelAnimationFrame(animationFrame);
  animationFrame = requestAnimationFrame(reconcile);
}

export function installSnapchatAccountSelectionControl() {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  if (installed) return false;
  installed = true;
  const observer = new MutationObserver(scheduleReconcile);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("popstate", scheduleReconcile);
  window.addEventListener("hashchange", scheduleReconcile);
  scheduleReconcile();
  return true;
}

export const SNAPCHAT_ACCOUNT_SELECTION_CONTROL_POLICY = Object.freeze({
  loads_selection_independently_from_campaign_report: true,
  remains_available_when_campaign_report_fails: true,
  requires_owner_selected_account: true,
  refreshes_report_after_save: true,
  preserves_report_date_range: true,
  provider_writes_allowed: false,
  campaign_writes_allowed: false,
  accounting_writes_allowed: false,
});

if (
  typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  installSnapchatAccountSelectionControl();
}
