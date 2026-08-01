import api from "./lib/api";
import {
  armReviewAutoAdvance,
  attemptReviewAutoAdvance,
  pendingReviewOrderRows,
  reviewOrderNumberFromHeading,
} from "./reviewAutoAdvance";


const ROOT_ID = "mezan-review-customer-waiting-root";
const STYLE_ID = "mezan-review-customer-waiting-style";
const RESUME_AFTER_RELOAD_KEY = "mezan.resumeCustomerReviewOrder";
let activeTab = "pending";
let waitingItems = [];
let waitingByNumber = new Map();
let loadingWaiting = false;
let scheduled = false;
let refreshTimer = null;

const text = (value) => String(value || "").trim();

export const WAITING_CUSTOMER_REVIEW_CSS = `
  [data-review-customer-waiting-drawer="true"]
    [data-testid="order-review-product-card"] button,
  [data-review-customer-waiting-drawer="true"]
    [data-testid="order-review-operational-item"] button,
  [data-review-customer-waiting-drawer="true"] [data-mezan-image-tools],
  [data-review-customer-waiting-drawer="true"] [data-review-edit-control] {
    display:none !important;
  }
`;

export function waitingCustomerActionLabel(isWaiting) {
  return isWaiting
    ? "إرجاع لانتظار المراجعة"
    : "انتظار مراجعة العميل";
}

export function reviewQueueTabVisibility(tab) {
  return {
    pendingHidden: tab === "customer",
    customerHidden: tab !== "customer",
  };
}

export function waitingCustomerCount(items) {
  return Array.isArray(items) ? items.length : 0;
}

function paymentText(order) {
  return text(order?.payment?.method_native || order?.payment?.method)
    || "غير محدد";
}

function orderNumberFromRowButton(button) {
  const node = [...(button?.querySelectorAll?.("span") || [])].find((span) =>
    /^#\d+$/.test(text(span.textContent)),
  );
  return text(node?.textContent).replace(/^#/, "");
}

function allReactQueueRows(root = document) {
  return [...root.querySelectorAll("button")]
    .filter((button) => !button.dataset.reviewWaitingCustomerRow)
    .map((button) => {
      const orderNumber = orderNumberFromRowButton(button);
      return orderNumber ? { orderNumber, button } : null;
    })
    .filter(Boolean);
}

function pageHeader(root = document) {
  const heading = [...root.querySelectorAll("h1")].find((node) =>
    text(node.textContent).includes("طلبات بانتظار المراجعة"),
  );
  return heading?.closest("header") || null;
}

function pendingTableSection(root = document) {
  return [...root.querySelectorAll("section")].find((section) =>
    [...section.querySelectorAll("button")].some((button) =>
      Boolean(orderNumberFromRowButton(button)),
    ),
  ) || null;
}

function tabButton(label, value) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.reviewQueueTab = value;
  button.style.cssText = [
    "display:inline-flex",
    "align-items:center",
    "gap:8px",
    "border:1px solid #ddd6fe",
    "border-radius:12px",
    "padding:9px 13px",
    "background:white",
    "color:#5b21b6",
    "font-size:13px",
    "font-weight:900",
    "cursor:pointer",
  ].join(";");
  const title = document.createElement("span");
  title.textContent = label;
  button.appendChild(title);
  return button;
}

function tabsHost(header) {
  let host = header.querySelector("[data-review-queue-tabs]");
  if (host) return host;
  host = document.createElement("div");
  host.dataset.reviewQueueTabs = "1";
  host.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin-top:14px";
  host.append(
    tabButton("انتظار المراجعة", "pending"),
    tabButton("انتظار مراجعة العميل", "customer"),
  );
  header.appendChild(host);
  return host;
}

function waitingBadge(button) {
  let badge = button.querySelector("[data-review-waiting-count]");
  if (!badge) {
    badge = document.createElement("span");
    badge.dataset.reviewWaitingCount = "1";
    badge.style.cssText = [
      "display:inline-flex",
      "align-items:center",
      "justify-content:center",
      "min-width:23px",
      "height:23px",
      "padding:0 6px",
      "border-radius:9999px",
      "background:#dc2626",
      "color:white",
      "font-size:12px",
      "font-weight:950",
      "font-variant-numeric:tabular-nums",
      "box-shadow:0 4px 10px rgba(220,38,38,.28)",
    ].join(";");
    button.appendChild(badge);
  }
  return badge;
}

function styleActiveTab(host) {
  host.querySelectorAll("[data-review-queue-tab]").forEach((button) => {
    const selected = button.dataset.reviewQueueTab === activeTab;
    button.setAttribute("aria-selected", selected ? "true" : "false");
    button.style.background = selected ? "#6d28d9" : "white";
    button.style.color = selected ? "white" : "#5b21b6";
    button.style.borderColor = selected ? "#6d28d9" : "#ddd6fe";
  });
  const customer = host.querySelector('[data-review-queue-tab="customer"]');
  waitingBadge(customer).textContent = String(waitingItems.length);
}

function waitingSection(pendingSection) {
  let section = document.querySelector('[data-review-queue-section="customer"]');
  if (section) return section;
  section = document.createElement("section");
  section.dataset.reviewQueueSection = "customer";
  section.style.cssText = "overflow:hidden;border:1px solid #e2e8f0;border-radius:16px;background:white;box-shadow:0 1px 3px rgba(15,23,42,.08)";
  pendingSection?.parentElement?.insertBefore(section, pendingSection.nextSibling);
  return section;
}

function waitingRow(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.reviewWaitingCustomerRow = "1";
  button.dataset.orderNumber = text(item.order_number);
  button.style.cssText = [
    "display:grid",
    "width:100%",
    "gap:8px",
    "border:0",
    "border-bottom:1px solid #e2e8f0",
    "padding:16px",
    "background:#fff7ed",
    "text-align:right",
    "cursor:pointer",
    "grid-template-columns:minmax(90px,.7fr) minmax(130px,1fr) minmax(130px,1fr) minmax(120px,1fr)",
    "align-items:center",
  ].join(";");
  const orderNumber = document.createElement("span");
  orderNumber.textContent = `#${text(item.order_number)}`;
  orderNumber.style.fontWeight = "900";
  const created = document.createElement("span");
  created.textContent = item.created_at
    ? new Date(item.created_at).toLocaleString("ar-SA")
    : "—";
  const payment = document.createElement("span");
  payment.textContent = paymentText(item);
  payment.style.fontWeight = "800";
  const customer = document.createElement("span");
  customer.textContent = text(item.customer?.name) || "عميل بدون اسم";
  button.append(orderNumber, created, payment, customer);
  button.onclick = () => openWaitingOrder(item);
  return button;
}

function renderWaitingSection(section) {
  section.innerHTML = "";
  const header = document.createElement("div");
  header.style.cssText = "display:grid;grid-template-columns:minmax(90px,.7fr) minmax(130px,1fr) minmax(130px,1fr) minmax(120px,1fr);gap:8px;padding:12px 16px;background:#f8fafc;color:#475569;font-size:13px;font-weight:900";
  ["رقم الطلب", "تاريخ الطلب", "طريقة الدفع", "العميل"].forEach((label) => {
    const node = document.createElement("div");
    node.textContent = label;
    header.appendChild(node);
  });
  section.appendChild(header);
  if (!waitingItems.length) {
    const empty = document.createElement("div");
    empty.textContent = "لا توجد طلبات بانتظار مراجعة العميل";
    empty.style.cssText = "display:flex;min-height:220px;align-items:center;justify-content:center;color:#64748b";
    section.appendChild(empty);
    return;
  }
  waitingItems.forEach((item) => section.appendChild(waitingRow(item)));
}

function applyQueueVisibility(root = document) {
  const header = pageHeader(root);
  const pendingSection = pendingTableSection(root);
  if (!header || !pendingSection) return null;
  pendingSection.dataset.reviewQueueSection = "pending";
  const customerSection = waitingSection(pendingSection);
  const visibility = reviewQueueTabVisibility(activeTab);
  pendingSection.hidden = visibility.pendingHidden;
  pendingSection.dataset.reviewQueueHidden = visibility.pendingHidden ? "true" : "false";
  customerSection.hidden = visibility.customerHidden;
  customerSection.dataset.reviewQueueHidden = visibility.customerHidden ? "true" : "false";

  allReactQueueRows(root).forEach(({ orderNumber, button }) => {
    const waiting = waitingByNumber.has(orderNumber);
    button.dataset.reviewCustomerWaiting = waiting ? "true" : "false";
    button.dataset.reviewQueueHidden = waiting ? "true" : "false";
    button.hidden = waiting;
  });

  const tabs = tabsHost(header);
  tabs.querySelectorAll("[data-review-queue-tab]").forEach((button) => {
    button.onclick = () => {
      activeTab = button.dataset.reviewQueueTab;
      applyQueueVisibility(root);
      scheduleDecorate();
    };
  });
  styleActiveTab(tabs);
  renderWaitingSection(customerSection);
  return { tabs, pendingSection, customerSection };
}

async function fetchReviewDetail(orderNumber) {
  const { data } = await api.get(
    `/order-reviews-v1/${encodeURIComponent(orderNumber)}`,
  );
  return data;
}

async function moveStage(orderNumber, mode) {
  const detail = await fetchReviewDetail(orderNumber);
  const path = mode === "wait" ? "wait" : "resume";
  const { data } = await api.post(
    `/order-review-customer-waiting-v1/${encodeURIComponent(orderNumber)}/${path}`,
    { expected_revision: Number(detail?.revision || 0) },
  );
  return { data, detail };
}

function toast(message, error = false) {
  const node = document.createElement("div");
  node.textContent = message;
  node.style.cssText = `position:fixed;z-index:12000;bottom:24px;left:24px;max-width:440px;padding:12px 16px;border-radius:14px;color:white;font-weight:900;background:${error ? "#be123c" : "#047857"};box-shadow:0 12px 30px #0003`;
  document.body.appendChild(node);
  window.setTimeout(() => node.remove(), 3500);
}

function originalReactRow(orderNumber) {
  return allReactQueueRows().find((row) => row.orderNumber === orderNumber) || null;
}

function drawerHeader(root = document) {
  const heading = [...root.querySelectorAll("h2")].find((node) =>
    text(node.textContent).includes("مراجعة الطلب #"),
  );
  return heading?.closest("header") || null;
}

function closeDrawer(header) {
  const button = [...(header?.children || [])].find((node) =>
    node.tagName === "BUTTON"
    && !node.dataset.reviewCustomerWaitingAction,
  );
  button?.click();
}

function completeReviewButton(root = document) {
  return [...root.querySelectorAll("button")].find((button) =>
    text(button.textContent).replace(/\s+/g, " ").includes("تمت المراجعة"),
  ) || null;
}

function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = WAITING_CUSTOMER_REVIEW_CSS;
  document.head.appendChild(style);
}

function customerActionButton(header) {
  let button = header.querySelector("[data-review-customer-waiting-action]");
  if (button) return button;
  button = document.createElement("button");
  button.type = "button";
  button.dataset.reviewCustomerWaitingAction = "1";
  button.style.cssText = [
    "display:inline-flex",
    "align-items:center",
    "justify-content:center",
    "border:1px solid #fb923c",
    "border-radius:12px",
    "padding:9px 13px",
    "background:#fff7ed",
    "color:#9a3412",
    "font-size:13px",
    "font-weight:900",
    "cursor:pointer",
    "white-space:nowrap",
  ].join(";");
  const navigation = header.querySelector("[data-review-manual-navigation]");
  if (navigation) header.insertBefore(button, navigation);
  else {
    const close = [...header.children].find((node) => node.tagName === "BUTTON");
    if (close) header.insertBefore(button, close);
    else header.appendChild(button);
  }
  return button;
}

async function handleCustomerAction(orderNumber, isWaiting, button) {
  if (button.disabled) return;
  const confirmed = window.confirm(
    isWaiting
      ? "إرجاع الطلب إلى قائمة انتظار المراجعة؟"
      : "نقل الطلب إلى انتظار مراجعة العميل داخل ميزان فقط؟",
  );
  if (!confirmed) return;
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "جارٍ الحفظ…";
  try {
    if (!isWaiting) {
      const rows = pendingReviewOrderRows();
      armReviewAutoAdvance(orderNumber, rows);
      const { detail } = await moveStage(orderNumber, "wait");
      const summary = {
        ...(detail?.order || {}),
        order_number: orderNumber,
        revision: Number(detail?.revision || 0) + 1,
        stage: "waiting_customer_review",
        waiting_customer_review_at: new Date().toISOString(),
      };
      waitingByNumber.set(orderNumber, summary);
      waitingItems = [
        summary,
        ...waitingItems.filter((item) => text(item.order_number) !== orderNumber),
      ];
      applyQueueVisibility();
      closeDrawer(drawerHeader());
      window.setTimeout(() => attemptReviewAutoAdvance(), 160);
      window.setTimeout(loadWaiting, 400);
      toast("تم نقل الطلب إلى انتظار مراجعة العميل في ميزان فقط.");
      return;
    }

    await moveStage(orderNumber, "resume");
    waitingByNumber.delete(orderNumber);
    waitingItems = waitingItems.filter(
      (item) => text(item.order_number) !== orderNumber,
    );
    applyQueueVisibility();
    decorateDrawer();
    window.setTimeout(loadWaiting, 250);
    toast("تمت إعادة الطلب إلى انتظار المراجعة.");
  } catch (error) {
    toast(
      error?.response?.data?.detail?.message
        || error?.message
        || "تعذر تغيير حالة الطلب.",
      true,
    );
    button.disabled = false;
    button.textContent = original;
  }
}

function decorateDrawer(root = document) {
  const orderNumber = reviewOrderNumberFromHeading(root);
  const header = drawerHeader(root);
  if (!orderNumber || !header) return null;
  const waiting = waitingByNumber.has(orderNumber);
  const drawer = header.closest("section");
  if (drawer) {
    drawer.dataset.reviewCustomerWaitingDrawer = waiting ? "true" : "false";
  }
  const complete = completeReviewButton(root);
  if (complete) {
    complete.dataset.reviewCustomerCompleteAction = "1";
    complete.hidden = false;
    complete.title = waiting
      ? "اعتماد الطلب مباشرة بعد اكتمال مراجعة العميل"
      : "اعتماد مراجعة الطلب";
  }

  const action = customerActionButton(header);
  action.textContent = waitingCustomerActionLabel(waiting);
  action.style.background = waiting ? "#fef2f2" : "#fff7ed";
  action.style.borderColor = waiting ? "#f87171" : "#fb923c";
  action.style.color = waiting ? "#b91c1c" : "#9a3412";
  action.disabled = false;
  action.onclick = () => handleCustomerAction(orderNumber, waiting, action);
  return action;
}

function isCompleteAction(button) {
  return Boolean(button)
    && text(button.textContent).replace(/\s+/g, " ").includes("تمت المراجعة");
}

function captureWaitingCompletion(event) {
  const button = event.target?.closest?.("button");
  if (!isCompleteAction(button)) return;
  const drawer = button.closest("section");
  if (drawer?.dataset.reviewCustomerWaitingDrawer !== "true") return;
  [450, 1000, 2000, 4000, 7000].forEach((delay) => {
    window.setTimeout(loadWaiting, delay);
  });
}

async function resumeAndOpen(item, overlay) {
  const orderNumber = text(item.order_number);
  try {
    await moveStage(orderNumber, "resume");
    sessionStorage.setItem(RESUME_AFTER_RELOAD_KEY, orderNumber);
    overlay.remove();
    window.location.reload();
  } catch (error) {
    toast(
      error?.response?.data?.detail?.message || "تعذر استئناف المراجعة.",
      true,
    );
  }
}

function showWaitingSummary(item) {
  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;z-index:11000;background:#02061799;display:flex;align-items:center;justify-content:center;padding:16px;direction:rtl";
  const panel = document.createElement("div");
  panel.style.cssText = "width:min(560px,100%);background:white;border-radius:22px;padding:20px;box-shadow:0 24px 70px #0005";
  const title = document.createElement("h2");
  title.textContent = `انتظار مراجعة العميل — #${text(item.order_number)}`;
  title.style.cssText = "margin:0;font-size:21px;font-weight:950";
  const info = document.createElement("div");
  info.style.cssText = "display:grid;gap:10px;margin-top:16px";
  [
    ["العميل", text(item.customer?.name) || "—"],
    ["الجوال", text(item.customer?.mobile) || "—"],
    ["طريقة الدفع", paymentText(item)],
    ["منذ", item.waiting_customer_review_at ? new Date(item.waiting_customer_review_at).toLocaleString("ar-SA") : "—"],
  ].forEach(([label, value]) => {
    const row = document.createElement("div");
    row.style.cssText = "padding:11px;border-radius:11px;background:#f8fafc";
    row.innerHTML = `<b>${label}:</b> ${value}`;
    info.appendChild(row);
  });
  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;gap:9px;justify-content:flex-start;margin-top:18px";
  const resume = document.createElement("button");
  resume.type = "button";
  resume.textContent = "إرجاع وفتح المراجعة";
  resume.style.cssText = "border:0;border-radius:12px;padding:11px 16px;background:#6d28d9;color:white;font-weight:900";
  resume.onclick = () => resumeAndOpen(item, overlay);
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "إغلاق";
  close.style.cssText = "border:1px solid #cbd5e1;border-radius:12px;padding:11px 16px;background:white;font-weight:900";
  close.onclick = () => overlay.remove();
  actions.append(resume, close);
  panel.append(title, info, actions);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function openWaitingOrder(item) {
  const orderNumber = text(item.order_number);
  const existing = originalReactRow(orderNumber);
  if (existing?.button) {
    existing.button.click();
    return;
  }
  showWaitingSummary(item);
}

async function loadWaiting() {
  if (loadingWaiting) return;
  loadingWaiting = true;
  try {
    const { data } = await api.get("/order-review-customer-waiting-v1", {
      params: { limit: 250 },
    });
    waitingItems = Array.isArray(data?.items) ? data.items : [];
    waitingByNumber = new Map(
      waitingItems.map((item) => [text(item.order_number), item]),
    );
    applyQueueVisibility();
    decorateDrawer();
  } catch (error) {
    console.warn("Customer waiting queue unavailable", error);
  } finally {
    loadingWaiting = false;
  }
}

function openResumedOrderAfterReload() {
  const orderNumber = sessionStorage.getItem(RESUME_AFTER_RELOAD_KEY);
  if (!orderNumber) return;
  const row = originalReactRow(orderNumber);
  if (!row?.button) return;
  sessionStorage.removeItem(RESUME_AFTER_RELOAD_KEY);
  activeTab = "pending";
  applyQueueVisibility();
  row.button.click();
}

function scheduleDecorate() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.requestAnimationFrame(() => {
    scheduled = false;
    injectStyle();
    applyQueueVisibility();
    decorateDrawer();
    openResumedOrderAfterReload();
  });
}

function start() {
  if (typeof document === "undefined" || !document.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);
  injectStyle();
  document.addEventListener("click", captureWaitingCompletion, true);
  const observer = new MutationObserver(scheduleDecorate);
  observer.observe(document.body, { childList: true, subtree: true });
  loadWaiting();
  scheduleDecorate();
  refreshTimer = window.setInterval(loadWaiting, 10_000);
  window.addEventListener("focus", loadWaiting);
}

if (
  typeof window !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
