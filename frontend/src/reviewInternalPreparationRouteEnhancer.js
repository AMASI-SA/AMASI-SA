import { toast } from "sonner";

import api from "./lib/api";


const ROOT_ID = "mezan-review-internal-preparation-route-root";
const OPERATIONAL_FULL_LABEL = "إضافة منتج تشغيلي";
let activeOrderNumber = null;
let activeDetail = null;
let controlsByItemId = new Map();
let assignableEmployees = [];
let loading = false;
let scheduled = false;

const text = (value) => String(value || "").trim();

export function findOperationalProductButton(card) {
  if (!card) return null;
  return [...card.querySelectorAll("button")].find((node) => {
    const fullLabel = text(node.dataset.reviewFullLabel);
    const ariaLabel = text(node.getAttribute("aria-label"));
    const visibleLabel = text(node.textContent);
    return fullLabel === OPERATIONAL_FULL_LABEL
      || ariaLabel === OPERATIONAL_FULL_LABEL
      || visibleLabel.includes(OPERATIONAL_FULL_LABEL);
  }) || null;
}

export function isInternalPreparationControl(control) {
  return control?.preparation_route === "internal_preparation"
    || control?.supplier_export === false;
}

export function internalPreparationActionLabel(control) {
  return isInternalPreparationControl(control)
    ? "إرجاع للملف"
    : "توجيه للتجهيز";
}

export function responsibleEmployeeId(control) {
  return text(
    control?.assigned_employee_id
    || control?.default_assigned_employee_id,
  );
}

export function responsibleEmployeeName(control, employees = []) {
  const employeeId = responsibleEmployeeId(control);
  const directName = text(control?.assigned_employee_name);
  if (directName) return directName;
  return text(
    employees.find((row) => text(row?.id) === employeeId)?.name,
  );
}

export function canSubmitResponsibleEmployee(employeeId) {
  return Boolean(text(employeeId));
}

function orderNumberFromPage() {
  if (typeof document === "undefined" || !document) return null;
  const heading = [...document.querySelectorAll("h2")].find(
    (node) => node.textContent?.includes("مراجعة الطلب #"),
  );
  return heading?.textContent?.match(/#(\d+)/)?.[1] || null;
}

function normalizeControl(control, orderItemId) {
  return {
    order_item_id: orderItemId,
    preparation_route: "supplier_file",
    supplier_export: true,
    preparation_status: "pending_file",
    assigned_employee_id: null,
    assigned_employee_name: null,
    assigned_employee_valid: false,
    default_assigned_employee_id: null,
    default_assigned_employee_name: null,
    ...(control || {}),
  };
}

async function loadContext(orderNumber) {
  if (!orderNumber || loading) return;
  loading = true;
  try {
    const [detailResponse, controlsResponse] = await Promise.all([
      api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`),
      api.get(`/order-review-export-controls-v1/${encodeURIComponent(orderNumber)}`),
    ]);
    activeOrderNumber = orderNumber;
    activeDetail = detailResponse.data;
    assignableEmployees = Array.isArray(controlsResponse.data?.employees)
      ? controlsResponse.data.employees
      : [];
    controlsByItemId = new Map(
      (controlsResponse.data?.items || []).map((row) => {
        const orderItemId = text(row.order_item_id);
        return [orderItemId, normalizeControl(row, orderItemId)];
      }),
    );
  } catch (error) {
    console.warn("Internal preparation route unavailable", error);
  } finally {
    loading = false;
  }
}

async function patchRoute(
  orderNumber,
  orderItemId,
  preparationRoute,
  {
    assignedEmployeeId = null,
    saveAssignmentAsDefault = true,
  } = {},
) {
  const payload = { preparation_route: preparationRoute };
  if (preparationRoute === "internal_preparation") {
    payload.assigned_employee_id = text(assignedEmployeeId);
    payload.save_assignment_as_default = Boolean(saveAssignmentAsDefault);
  }
  const { data } = await api.patch(
    `/order-review-export-controls-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}`,
    payload,
  );
  const next = normalizeControl(data, orderItemId);
  controlsByItemId.set(orderItemId, next);
  return next;
}

function actionStyle(internal) {
  return [
    "border-radius:10px",
    "padding:5px 9px",
    "font-size:11px",
    "font-weight:900",
    "cursor:pointer",
    "white-space:nowrap",
    internal ? "border:1px solid #10b981" : "border:1px solid #f59e0b",
    internal ? "background:white" : "background:#fffbeb",
    internal ? "color:#047857" : "color:#92400e",
  ].join(";");
}

function showResponsibleEmployeeModal(item, control) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.dataset.responsibleEmployeeModal = "1";
    overlay.style.cssText = [
      "position:fixed",
      "inset:0",
      "z-index:10030",
      "background:rgba(2,6,23,.64)",
      "display:flex",
      "align-items:center",
      "justify-content:center",
      "padding:16px",
      "direction:rtl",
    ].join(";");

    const panel = document.createElement("div");
    panel.style.cssText = [
      "width:min(520px,100%)",
      "max-height:92vh",
      "overflow:auto",
      "background:white",
      "border-radius:24px",
      "padding:20px",
      "box-shadow:0 24px 70px rgba(2,6,23,.35)",
    ].join(";");

    const header = document.createElement("div");
    header.style.cssText = "display:flex;justify-content:space-between;align-items:start;gap:12px";
    const heading = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = "تحديد موظف التجهيز المسؤول";
    title.style.cssText = "margin:0;font-size:21px;font-weight:900;color:#0f172a";
    const product = document.createElement("p");
    product.textContent = text(item?.name) || "المنتج";
    product.style.cssText = "margin:6px 0 0;color:#64748b;font-weight:700";
    heading.append(title, product);
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "×";
    close.style.cssText = "border:1px solid #cbd5e1;background:white;border-radius:10px;width:36px;height:36px;font-size:22px";
    header.append(heading, close);

    const label = document.createElement("label");
    label.textContent = "الموظف المسؤول";
    label.style.cssText = "display:block;margin-top:18px;font-weight:900;color:#334155";
    const select = document.createElement("select");
    select.style.cssText = "width:100%;margin-top:7px;padding:12px;border:1px solid #94a3b8;border-radius:12px;background:white;font-weight:800";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "اختر الموظف المسؤول";
    select.appendChild(placeholder);
    assignableEmployees.forEach((employee) => {
      const option = document.createElement("option");
      option.value = text(employee.id);
      option.textContent = text(employee.name)
        + (text(employee.role) ? ` — ${text(employee.role)}` : "");
      select.appendChild(option);
    });
    const initialEmployeeId = responsibleEmployeeId(control);
    if (initialEmployeeId && assignableEmployees.some(
      (row) => text(row.id) === initialEmployeeId,
    )) {
      select.value = initialEmployeeId;
    }
    label.appendChild(select);

    const emptyNotice = document.createElement("div");
    emptyNotice.textContent = "لا يوجد موظف يملك صلاحية إدارة التجهيز. أضف موظفًا أو امنحه صلاحية preparation.manage أولًا.";
    emptyNotice.style.cssText = "display:none;margin-top:10px;padding:10px;border-radius:10px;background:#fff1f2;color:#be123c;font-size:12px;font-weight:900;line-height:1.7";
    if (!assignableEmployees.length) emptyNotice.style.display = "block";

    const defaultLabel = document.createElement("label");
    defaultLabel.style.cssText = "display:flex;align-items:flex-start;gap:10px;margin-top:16px;padding:12px;border-radius:12px;background:#f0fdfa;color:#115e59;font-weight:800;line-height:1.7";
    const defaultCheckbox = document.createElement("input");
    defaultCheckbox.type = "checkbox";
    defaultCheckbox.checked = true;
    defaultCheckbox.style.marginTop = "5px";
    const defaultText = document.createElement("span");
    defaultText.textContent = "حفظ هذا الموظف كمسؤول افتراضي لنفس المنتج في جميع الطلبات القادمة";
    defaultLabel.append(defaultCheckbox, defaultText);

    const hint = document.createElement("div");
    hint.style.cssText = "margin-top:10px;color:#64748b;font-size:12px;line-height:1.7";
    hint.textContent = control?.assignment_source === "default"
      ? "تم اختيار المسؤول الافتراضي المحفوظ. يمكنك تغييره، وسيُحدّث الإعداد للطلبات القادمة ما دام الخيار مفعّلًا."
      : "ألغِ خيار الحفظ الافتراضي لتطبيق التغيير على هذا الطلب فقط.";

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:10px;justify-content:flex-start;margin-top:20px";
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.textContent = "تأكيد التوجيه";
    confirm.disabled = !assignableEmployees.length;
    confirm.style.cssText = "border:0;border-radius:12px;padding:11px 18px;background:#047857;color:white;font-weight:900;opacity:"
      + (assignableEmployees.length ? "1" : ".45");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "إلغاء";
    cancel.style.cssText = "border:1px solid #cbd5e1;border-radius:12px;padding:11px 18px;background:white;color:#334155;font-weight:900";
    actions.append(confirm, cancel);

    const finish = (value) => {
      overlay.remove();
      resolve(value);
    };
    close.onclick = () => finish(null);
    cancel.onclick = () => finish(null);
    overlay.onclick = (event) => {
      if (event.target === overlay) finish(null);
    };
    confirm.onclick = () => {
      if (!canSubmitResponsibleEmployee(select.value)) {
        select.style.borderColor = "#e11d48";
        toast.error("يجب تحديد الموظف المسؤول.");
        return;
      }
      finish({
        employeeId: text(select.value),
        saveAsDefault: defaultCheckbox.checked,
      });
    };

    panel.append(
      header,
      label,
      emptyNotice,
      defaultLabel,
      hint,
      actions,
    );
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    select.focus();
  });
}

function renderAssignmentBanner(card, item, control, host) {
  const internal = isInternalPreparationControl(control);
  let banner = card.querySelector("[data-item-route-banner]");
  if (!internal) {
    if (banner) banner.hidden = true;
    return;
  }
  if (!banner) {
    banner = document.createElement("div");
    banner.dataset.itemRouteBanner = "1";
    banner.style.cssText = "margin:0 16px 12px;border:1px solid #f59e0b;border-radius:12px;background:#fffbeb;color:#92400e;padding:10px 12px;font-size:12px;font-weight:900;line-height:1.7";
    const footer = host.closest(".border-t") || host.parentElement;
    footer?.parentElement?.insertBefore(banner, footer);
  }
  banner.hidden = false;
  banner.innerHTML = "";

  const summary = document.createElement("div");
  const employeeName = responsibleEmployeeName(control, assignableEmployees);
  summary.textContent = employeeName
    ? `تجهيز داخلي — المسؤول: ${employeeName}. لن يظهر المنتج في ملف المورد، وسيبدأ بحالة قيد التجهيز.`
    : "تجهيز داخلي — الموظف المسؤول غير متاح. يجب اختيار موظف بديل.";
  const change = document.createElement("button");
  change.type = "button";
  change.textContent = "تغيير المسؤول";
  change.style.cssText = "margin-top:8px;border:1px solid #f59e0b;background:white;color:#92400e;border-radius:9px;padding:5px 9px;font-weight:900";
  change.onclick = async () => {
    const selection = await showResponsibleEmployeeModal(item, control);
    if (!selection) return;
    change.disabled = true;
    change.textContent = "جارٍ الحفظ…";
    try {
      const next = await patchRoute(
        activeOrderNumber,
        text(item.order_item_id),
        "internal_preparation",
        {
          assignedEmployeeId: selection.employeeId,
          saveAssignmentAsDefault: selection.saveAsDefault,
        },
      );
      toast.success(
        selection.saveAsDefault
          ? "تم تغيير المسؤول وحفظه للطلبات القادمة."
          : "تم تغيير المسؤول لهذا الطلب فقط.",
      );
      renderRouteAction(card, item, next);
    } catch (error) {
      toast.error(
        error?.response?.data?.detail?.message
        || error.message
        || "تعذّر تغيير الموظف المسؤول.",
      );
      change.disabled = false;
      change.textContent = "تغيير المسؤول";
    }
  };
  banner.append(summary, change);
}

function renderRouteAction(card, item, control) {
  const operationalButton = findOperationalProductButton(card);
  const host = operationalButton?.parentElement;
  if (!host) return false;

  const internal = isInternalPreparationControl(control);
  card.style.borderColor = internal ? "#f59e0b" : "#e2e8f0";
  card.style.boxShadow = internal ? "0 0 0 2px rgba(245,158,11,.13)" : "";

  let button = host.querySelector("[data-item-route-action]");
  if (!button) {
    button = document.createElement("button");
    button.type = "button";
    button.dataset.itemRouteAction = "1";
    host.appendChild(button);
  }

  const fullLabel = internal
    ? "إرجاع المنتج لملف التجهيز"
    : "توجيه مباشر للتجهيز الداخلي";
  button.textContent = internalPreparationActionLabel(control);
  button.title = fullLabel;
  button.setAttribute("aria-label", fullLabel);
  if (!internal) button.dataset.reviewFullLabel = fullLabel;
  else delete button.dataset.reviewFullLabel;
  button.style.cssText = actionStyle(internal);

  renderAssignmentBanner(card, item, control, host);

  button.onclick = async () => {
    button.disabled = true;
    const originalLabel = button.textContent;
    try {
      if (internal) {
        button.textContent = "جارٍ الحفظ…";
        const next = await patchRoute(
          activeOrderNumber,
          text(item.order_item_id),
          "supplier_file",
        );
        toast.success("تمت إعادة المنتج إلى ملف التجهيز.");
        renderRouteAction(card, item, next);
        return;
      }

      button.disabled = false;
      const selection = await showResponsibleEmployeeModal(item, control);
      if (!selection) return;
      button.disabled = true;
      button.textContent = "جارٍ الحفظ…";
      const next = await patchRoute(
        activeOrderNumber,
        text(item.order_item_id),
        "internal_preparation",
        {
          assignedEmployeeId: selection.employeeId,
          saveAssignmentAsDefault: selection.saveAsDefault,
        },
      );
      toast.success(
        selection.saveAsDefault
          ? "تم التوجيه وحفظ المسؤول للطلبات القادمة."
          : "تم التوجيه لهذا الطلب فقط.",
      );
      renderRouteAction(card, item, next);
    } catch (error) {
      toast.error(
        error?.response?.data?.detail?.message
        || error.message
        || "تعذّر تغيير مسار المنتج.",
      );
      button.disabled = false;
      button.textContent = originalLabel;
    }
  };
  button.disabled = false;
  return true;
}

async function enhance() {
  scheduled = false;
  if (typeof document === "undefined" || !document) return;
  const orderNumber = orderNumberFromPage();
  if (!orderNumber) return;
  if (orderNumber !== activeOrderNumber || !activeDetail) {
    await loadContext(orderNumber);
  }
  if (orderNumber !== activeOrderNumber || !activeDetail) return;

  const cards = [...document.querySelectorAll("[data-testid='order-review-product-card']")];
  const items = Array.isArray(activeDetail.items) ? activeDetail.items : [];
  cards.forEach((card, index) => {
    const item = items[index];
    const orderItemId = text(item?.order_item_id);
    if (!orderItemId) return;
    const control = controlsByItemId.get(orderItemId)
      || normalizeControl(null, orderItemId);
    renderRouteAction(card, item, control);
  });
}

function scheduleEnhance() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.setTimeout(enhance, 35);
}

function start() {
  if (typeof document === "undefined" || !document?.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);
  const observer = new MutationObserver(scheduleEnhance);
  observer.observe(document.body, { childList: true, subtree: true });
  scheduleEnhance();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
