import api from "./lib/api";

const PAGE_TEST_ID = "advanced-dashboard-page";
const CONTROL_ID = "dashboard-eligibility-control";
const PROFIT_NOTE_ID = "dashboard-eligibility-profit-note";
const PRODUCT_NOTE_ID = "dashboard-eligibility-product-note";

let settings = null;
let summary = null;
let summaryRequestKey = "";
let summaryRequestInFlight = false;
let renderScheduled = false;

function isAdvancedDashboard() {
  return Boolean(document.querySelector(`[data-testid="${PAGE_TEST_ID}"]`));
}

function dashboardQueryFromConfig(config = {}) {
  const rawUrl = String(config?.url || "");
  if (!rawUrl.includes("/dashboard-v2") || rawUrl.includes("eligibility-")) return null;
  const queryText = rawUrl.includes("?") ? rawUrl.split("?").slice(1).join("?") : "";
  const params = new URLSearchParams(queryText);
  return {
    from_date: params.get("from_date") || "",
    to_date: params.get("to_date") || "",
    payment_methods: params.get("payment_methods") || "",
    shipping_companies: params.get("shipping_companies") || "",
  };
}

function summaryKey(query) {
  return JSON.stringify(query || {});
}

function setTextIfChanged(node, value) {
  if (!node) return;
  const next = String(value ?? "");
  if (node.textContent !== next) node.textContent = next;
}

function scheduleRender() {
  if (renderScheduled) return;
  renderScheduled = true;
  const run = () => {
    renderScheduled = false;
    renderEnhancements();
  };
  if (typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(run);
  } else {
    window.setTimeout(run, 0);
  }
}

async function loadSettings() {
  try {
    const { data } = await api.get("/dashboard-v2/eligibility-settings");
    settings = data || null;
  } catch {
    settings = null;
  }
  scheduleRender();
}

async function loadSummary(query) {
  if (!query || summaryRequestInFlight) return;
  const key = summaryKey(query);
  if (key === summaryRequestKey && summary) return;
  summaryRequestInFlight = true;
  try {
    const { data } = await api.get("/dashboard-v2/eligibility-summary", { params: query });
    summary = data || null;
    summaryRequestKey = key;
  } catch {
    summary = null;
    summaryRequestKey = key;
  } finally {
    summaryRequestInFlight = false;
    scheduleRender();
  }
}

function controlMarkup() {
  const enabled = settings?.enabled !== false;
  const stateLabel = enabled ? "يعمل" : "متوقف";
  const stateTone = enabled
    ? "background:#ecfdf5;color:#047857;border-color:#a7f3d0"
    : "background:#f8fafc;color:#64748b;border-color:#cbd5e1";
  return `
    <button type="button" data-dashboard-eligibility-toggle="true"
      style="display:flex;align-items:center;gap:8px;border:1px solid #cbd5e1;border-radius:12px;padding:8px 11px;background:#fff;font-size:11px;font-weight:800;cursor:pointer;white-space:nowrap">
      <span>فلتر التأهيل</span>
      <span style="border:1px solid;border-radius:999px;padding:2px 8px;${stateTone}">${stateLabel}</span>
    </button>`;
}

function installOrUpdateControl() {
  if (!isAdvancedDashboard()) return;
  const header = document.querySelector(`[data-testid="${PAGE_TEST_ID}"] > header`);
  if (!header) return;

  const enabledKey = settings?.enabled !== false ? "on" : "off";
  let wrapper = document.getElementById(CONTROL_ID);
  if (!wrapper) {
    wrapper = document.createElement("div");
    wrapper.id = CONTROL_ID;
    wrapper.style.display = "flex";
    wrapper.style.alignItems = "center";
    wrapper.style.gap = "8px";
    header.appendChild(wrapper);
  }

  if (wrapper.dataset.renderedState !== enabledKey) {
    wrapper.innerHTML = controlMarkup();
    wrapper.dataset.renderedState = enabledKey;
  }
}

function installProfitNote() {
  const panel = document.querySelector('[data-testid="advanced-profit-summary"]');
  if (!panel || !summary) return;
  const metrics = panel.children?.[1];
  if (!metrics) return;

  let note = document.getElementById(PROFIT_NOTE_ID);
  if (!note) {
    note = document.createElement("div");
    note.id = PROFIT_NOTE_ID;
    note.style.cssText = "padding:6px 12px;border-bottom:1px solid #d1fae5;background:#f0fdf4;font-size:10px;font-weight:700;color:#475569;text-align:right";
    metrics.insertAdjacentElement("afterend", note);
  }

  const text = summary.enabled
    ? `طلبات غير مؤهلة: ${Number(summary.excluded_orders_count || 0).toLocaleString("en-US")} طلب · أقل من ${Number(summary.order_min_total_sar || 50).toLocaleString("en-US")} ر.س · لا تدخل في الطلبات أو المبيعات أو المتوسطات`
    : "فلتر التأهيل متوقف · يتم احتساب جميع الطلبات والقطع في لوحة التحكم";
  setTextIfChanged(note, text);
}

function updateProductsHeader() {
  const panel = document.querySelector('[data-testid="advanced-top-products"]');
  if (!panel || !summary) return;
  const headerMeta = panel.querySelector("div > div.text-left");
  if (!headerMeta) return;

  const firstLine = headerMeta.querySelector("p");
  setTextIfChanged(
    firstLine,
    `${Number(summary.eligible_piece_count || 0).toLocaleString("en-US")} قطعة خلال الفترة`,
  );

  let note = document.getElementById(PRODUCT_NOTE_ID);
  if (!note) {
    note = document.createElement("p");
    note.id = PRODUCT_NOTE_ID;
    note.style.cssText = "font-size:9px;line-height:14px;color:#c7d2fe;font-weight:700";
    headerMeta.appendChild(note);
  }
  setTextIfChanged(
    note,
    summary.enabled
      ? `مستبعد ${Number(summary.excluded_low_price_piece_count || 0).toLocaleString("en-US")} قطعة · سعر الوحدة أقل من ${Number(summary.product_min_unit_sale_sar || 25).toLocaleString("en-US")} ر.س`
      : "فلتر القطع منخفضة السعر متوقف",
  );
}

function renderEnhancements() {
  if (!isAdvancedDashboard()) return;
  installOrUpdateControl();
  installProfitNote();
  updateProductsHeader();
}

async function handleToggleClick(event) {
  const button = event.target?.closest?.("[data-dashboard-eligibility-toggle]");
  if (!button || !isAdvancedDashboard()) return;
  const currentEnabled = settings?.enabled !== false;
  button.disabled = true;
  try {
    const { data } = await api.put("/dashboard-v2/eligibility-settings", { enabled: !currentEnabled });
    settings = data || { enabled: !currentEnabled };
    window.location.reload();
  } catch {
    button.disabled = false;
  }
}

api.interceptors.response.use((response) => {
  const query = dashboardQueryFromConfig(response?.config);
  if (query) window.setTimeout(() => loadSummary(query), 0);
  return response;
});

if (typeof window !== "undefined" && typeof document !== "undefined") {
  const observer = new MutationObserver(() => scheduleRender());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("click", handleToggleClick);
  window.addEventListener("popstate", scheduleRender);
  window.setTimeout(() => {
    loadSettings();
    scheduleRender();
  }, 0);
}
