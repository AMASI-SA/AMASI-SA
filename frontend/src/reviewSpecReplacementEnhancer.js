import { toast } from "sonner";

import api from "./lib/api";


const ROOT_ID = "mezan-review-spec-replacement-root";
let activeOrderNumber = null;
let activeDetail = null;
let replacementsByItemId = new Map();
let loading = false;
let scheduled = false;

const text = (value) => String(value || "").trim();

export function canonicalReviewSpecReplacementKey(value) {
  const normalized = text(value)
    .toLocaleLowerCase("ar")
    .replace(/[ـ:：\s_-]+/g, " ")
    .trim();
  if (["لون", "اللون", "لون المنتج"].includes(normalized)) return "color";
  if (["مقاس", "المقاس", "مقاس المنتج"].includes(normalized)) return "size";
  if (["خامة", "الخامة", "مادة", "المادة"].includes(normalized)) return "material";
  return normalized;
}

export function specReplacementPatchPath(orderNumber, orderItemId) {
  return `/order-review-spec-replacements-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}`;
}

export function specReplacementDisplayText(spec) {
  const replacement = text(spec?.replacement_text);
  return replacement ? `النص البديل للملف: ${replacement}` : "";
}

export function specReplacementButtonLabel(spec) {
  return text(spec?.replacement_text) ? "تعديل البديل" : "نص بديل";
}

function orderNumberFromPage() {
  if (typeof document === "undefined") return null;
  const heading = [...document.querySelectorAll("h2")].find(
    (node) => node.textContent?.includes("مراجعة الطلب #"),
  );
  return heading?.textContent?.match(/#(\d+)/)?.[1] || null;
}

async function loadContext(orderNumber) {
  if (!orderNumber || loading) return;
  loading = true;
  try {
    const [detailResponse, replacementResponse] = await Promise.all([
      api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`),
      api.post(
        `/order-review-spec-replacements-v1/${encodeURIComponent(orderNumber)}/materialize-defaults`,
      ).catch(async () => api.get(
        `/order-review-spec-replacements-v1/${encodeURIComponent(orderNumber)}`,
      )),
    ]);
    activeOrderNumber = orderNumber;
    activeDetail = detailResponse.data;
    replacementsByItemId = new Map(
      (replacementResponse.data?.items || []).map((row) => [
        text(row.order_item_id),
        row,
      ]),
    );
  } catch (error) {
    console.warn("Supplier spec replacements unavailable", error);
  } finally {
    loading = false;
  }
}

function showReplacementModal(item, spec) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.dataset.specReplacementModal = "1";
    overlay.style.cssText = [
      "position:fixed",
      "inset:0",
      "z-index:10040",
      "background:rgba(2,6,23,.66)",
      "display:flex",
      "align-items:center",
      "justify-content:center",
      "padding:16px",
      "direction:rtl",
    ].join(";");

    const panel = document.createElement("div");
    panel.style.cssText = [
      "width:min(560px,100%)",
      "background:white",
      "border-radius:24px",
      "padding:20px",
      "box-shadow:0 24px 70px rgba(2,6,23,.35)",
    ].join(";");

    const title = document.createElement("h2");
    title.textContent = "نص بديل في ملف التجهيز";
    title.style.cssText = "margin:0;color:#0f172a;font-size:21px;font-weight:900";

    const productName = document.createElement("p");
    productName.textContent = text(item?.name) || "المنتج";
    productName.style.cssText = "margin:6px 0 0;color:#64748b;font-weight:700";

    const original = document.createElement("div");
    original.textContent = `النص الأصلي: ${text(spec?.original_text)}`;
    original.style.cssText = "margin-top:16px;padding:11px 12px;border-radius:12px;background:#f5f3ff;color:#4c1d95;font-weight:900";

    const label = document.createElement("label");
    label.textContent = "النص الذي سيظهر بدلًا منه في الملف فقط";
    label.style.cssText = "display:block;margin-top:16px;color:#334155;font-weight:900";

    const input = document.createElement("textarea");
    input.value = text(spec?.replacement_text);
    input.placeholder = text(spec?.original_text).replace(":", "");
    input.maxLength = 500;
    input.rows = 3;
    input.style.cssText = "width:100%;box-sizing:border-box;margin-top:7px;padding:12px;border:1px solid #94a3b8;border-radius:12px;resize:vertical;font:inherit;font-weight:800";
    label.appendChild(input);

    const persistentLabel = document.createElement("label");
    persistentLabel.style.cssText = "display:flex;align-items:flex-start;gap:10px;margin-top:14px;padding:12px;border-radius:12px;background:#f0fdfa;color:#115e59;font-weight:800;line-height:1.7";
    const persistent = document.createElement("input");
    persistent.type = "checkbox";
    persistent.checked = true;
    persistent.style.marginTop = "5px";
    const persistentText = document.createElement("span");
    persistentText.textContent = "حفظه لنفس المنتج والمواصفة في الطلبات القادمة";
    persistentLabel.append(persistent, persistentText);

    const note = document.createElement("p");
    note.textContent = "ستبقى مواصفة سلة الأصلية ظاهرة هنا كما هي؛ التغيير يخص ملف التجهيز فقط.";
    note.style.cssText = "margin:10px 0 0;color:#64748b;font-size:12px;line-height:1.7";

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:9px;flex-wrap:wrap;margin-top:18px";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "حفظ النص البديل";
    save.style.cssText = "border:0;border-radius:12px;padding:10px 15px;background:#7c3aed;color:white;font-weight:900";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "حذف النص البديل";
    remove.hidden = !text(spec?.replacement_text);
    remove.style.cssText = "border:1px solid #fda4af;border-radius:12px;padding:10px 15px;background:white;color:#be123c;font-weight:900";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "إلغاء";
    cancel.style.cssText = "border:1px solid #cbd5e1;border-radius:12px;padding:10px 15px;background:white;color:#334155;font-weight:900";
    actions.append(save, remove, cancel);

    const finish = (value) => {
      overlay.remove();
      resolve(value);
    };
    cancel.onclick = () => finish(null);
    overlay.onclick = (event) => {
      if (event.target === overlay) finish(null);
    };
    save.onclick = () => {
      const replacementText = text(input.value);
      if (!replacementText) {
        input.style.borderColor = "#e11d48";
        toast.error("اكتب النص البديل أو استخدم زر حذف النص البديل.");
        return;
      }
      finish({
        replacementText,
        saveAsDefault: persistent.checked,
      });
    };
    remove.onclick = () => finish({
      replacementText: null,
      saveAsDefault: persistent.checked,
    });

    panel.append(title, productName, original, label, persistentLabel, note, actions);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    input.focus();
  });
}

async function patchReplacement(item, spec, selection) {
  const { data } = await api.patch(
    specReplacementPatchPath(activeOrderNumber, item.order_item_id),
    {
      spec_key: spec.spec_key,
      replacement_text: selection.replacementText,
      save_as_default: selection.saveAsDefault,
    },
  );
  replacementsByItemId.set(text(item.order_item_id), data.item);
  return data.item;
}

function renderSpecTools(row, item, spec) {
  let tools = row.querySelector("[data-spec-replacement-tools]");
  const signature = JSON.stringify({
    specKey: text(spec?.spec_key),
    replacementText: text(spec?.replacement_text),
    replacementSource: text(spec?.replacement_source),
  });
  if (tools?.dataset.specReplacementSignature === signature) return;
  if (!tools) {
    tools = document.createElement("div");
    tools.dataset.specReplacementTools = "1";
    row.appendChild(tools);
  }
  tools.dataset.specReplacementSignature = signature;
  tools.style.cssText = [
    "grid-column:1/-1",
    "display:flex",
    "align-items:center",
    "justify-content:space-between",
    "gap:8px",
    "flex-wrap:wrap",
    "margin-top:5px",
  ].join(";");
  tools.innerHTML = "";

  const display = document.createElement("div");
  display.dataset.specReplacementDisplay = "1";
  display.textContent = specReplacementDisplayText(spec);
  display.hidden = !display.textContent;
  display.style.cssText = "color:#6d28d9;font-size:11px;font-weight:900;line-height:1.6";

  const button = document.createElement("button");
  button.type = "button";
  button.dataset.specReplacementAction = "1";
  button.textContent = specReplacementButtonLabel(spec);
  button.title = "إضافة نص يظهر بدل هذه المواصفة في ملف التجهيز فقط";
  button.style.cssText = "border:1px solid #8b5cf6;border-radius:9px;padding:4px 8px;background:white;color:#6d28d9;font-size:10px;font-weight:900;white-space:nowrap";
  button.onclick = async () => {
    const selection = await showReplacementModal(item, spec);
    if (!selection) return;
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = "جارٍ الحفظ…";
    try {
      const nextItem = await patchReplacement(item, spec, selection);
      const nextSpec = (nextItem.specs || []).find(
        (rowSpec) => text(rowSpec.spec_key) === text(spec.spec_key),
      ) || spec;
      toast.success(
        selection.replacementText
          ? selection.saveAsDefault
            ? "تم حفظ النص البديل للملف والطلبات القادمة."
            : "تم حفظ النص البديل لهذا الطلب فقط."
          : selection.saveAsDefault
            ? "تم حذف النص البديل من هذا الطلب والطلبات القادمة."
            : "تم حذف النص البديل من هذا الطلب فقط.",
      );
      renderSpecTools(row, item, nextSpec);
    } catch (error) {
      toast.error(
        error?.response?.data?.detail?.message
        || error.message
        || "تعذّر حفظ النص البديل.",
      );
      button.disabled = false;
      button.textContent = originalLabel;
    }
  };

  tools.append(display, button);
}

function enhanceCard(card, item) {
  const itemState = replacementsByItemId.get(text(item?.order_item_id));
  if (!itemState) return;
  const specsByKey = new Map(
    (itemState.specs || []).map((spec) => [text(spec.spec_key), spec]),
  );
  const specsContainer = card.querySelector("[data-testid='order-review-product-specs']");
  if (!specsContainer) return;
  [...specsContainer.children].forEach((row) => {
    const label = text(row.querySelector("span")?.textContent).replace(/[:：]\s*$/, "");
    const specKey = canonicalReviewSpecReplacementKey(label);
    const spec = specsByKey.get(specKey);
    if (spec) renderSpecTools(row, item, spec);
  });
}

async function enhance() {
  scheduled = false;
  if (typeof document === "undefined") return;
  const orderNumber = orderNumberFromPage();
  if (!orderNumber) return;
  if (orderNumber !== activeOrderNumber || !activeDetail) {
    await loadContext(orderNumber);
  }
  if (orderNumber !== activeOrderNumber || !activeDetail) return;

  const cards = [...document.querySelectorAll("[data-testid='order-review-product-card']")];
  const items = Array.isArray(activeDetail.items) ? activeDetail.items : [];
  cards.forEach((card, index) => enhanceCard(card, items[index]));
}

function scheduleEnhance() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.setTimeout(enhance, 45);
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
