import api from "./lib/api";
import { listReviewedProductCatalog } from "./services/orderReviewEngine";
import {
    findReviewedProductForCard,
    reviewedProductSortButtonLabel,
    reviewedProductSortCandidateSummary,
    updateReviewedProductSortPreference,
} from "./reviewedProductSortUi";

const ROOT_ID = "mezan-reviewed-product-sort-enhancer";
const BUTTON_ATTR = "data-reviewed-product-sort-button";
let products = [];
let loadingPromise = null;
let scanScheduled = false;
let modalOpen = false;

const text = (value) => String(value || "").trim();

function node(tag, content = "") {
    const element = document.createElement(tag);
    if (content) element.textContent = content;
    return element;
}

async function loadProducts({ force = false } = {}) {
    if (force) loadingPromise = null;
    if (loadingPromise) return loadingPromise;
    loadingPromise = listReviewedProductCatalog({ limit: 2000 })
        .then((catalog) => {
            products = Array.isArray(catalog?.products) ? catalog.products : [];
            return products;
        })
        .catch((error) => {
            loadingPromise = null;
            throw error;
        });
    return loadingPromise;
}

function cardIdentity(card) {
    const name = text(card.querySelector("h3")?.textContent);
    const skuNode = [...card.querySelectorAll('[dir="ltr"]')].find((entry) =>
        text(entry.textContent).toUpperCase().startsWith("SKU:"),
    );
    const sku = text(skuNode?.textContent).replace(/^SKU:\s*/i, "");
    return { name, sku };
}

function updateButton(button, product) {
    const label = reviewedProductSortButtonLabel(product);
    if (button.textContent !== label) button.textContent = label;
    button.dataset.groupKey = text(product?.group_key);
    const candidates = Array.isArray(product?.preparation_sort_candidates)
        ? product.preparation_sort_candidates
        : [];
    button.disabled = candidates.length === 0;
    button.title = candidates.length
        ? "اختر مواصفة واحدة لترتيب بطاقات هذا المنتج داخل ملف التجهيز"
        : "لا توجد مواصفات قابلة للترتيب في بطاقات هذا المنتج";
    button.style.opacity = candidates.length ? "1" : ".55";
    button.style.cursor = candidates.length ? "pointer" : "not-allowed";
}

function closeModal(overlay) {
    modalOpen = false;
    overlay.remove();
}

function openSortModal(product) {
    if (modalOpen) return;
    modalOpen = true;

    const candidates = Array.isArray(product?.preparation_sort_candidates)
        ? product.preparation_sort_candidates
        : [];
    let selected = text(product?.preparation_sort_spec);

    const overlay = node("div");
    overlay.dataset.reviewedProductSortModal = "1";
    overlay.style.cssText = "position:fixed;inset:0;z-index:15000;background:#02061799;display:flex;align-items:center;justify-content:center;padding:14px;direction:rtl";
    const panel = node("div");
    panel.style.cssText = "width:min(620px,100%);max-height:92vh;overflow:auto;border-radius:22px;background:white;padding:20px;box-shadow:0 28px 90px #0005";

    const header = node("div");
    header.style.cssText = "display:flex;align-items:flex-start;justify-content:space-between;gap:12px";
    const headerText = node("div");
    const heading = node("h2", "ترتيب بطاقات المنتج في الملف");
    heading.style.cssText = "margin:0;font-size:21px;font-weight:950;color:#0f172a";
    const productName = node("div", text(product?.name) || "منتج");
    productName.style.cssText = "margin-top:4px;font-size:13px;font-weight:850;color:#6d28d9";
    const description = node(
        "p",
        "اختر مواصفة واحدة. ستظهر القيمة صاحبة أكبر عدد من القطع أولًا، ثم القيمة التالية، مع بقاء كل سطر منتج من سلة بطاقة واحدة وكميته كما هي.",
    );
    description.style.cssText = "margin:8px 0 0;color:#64748b;font-size:12px;line-height:1.8";
    headerText.append(heading, productName, description);
    const close = node("button", "×");
    close.type = "button";
    close.style.cssText = "border:0;background:#f1f5f9;width:36px;height:36px;border-radius:10px;font-size:25px;line-height:1;color:#475569";
    close.onclick = () => closeModal(overlay);
    header.append(headerText, close);

    const choices = node("div");
    choices.style.cssText = "display:grid;gap:9px;margin-top:17px";

    function choiceRow({ key, label, summary }) {
        const row = node("label");
        row.style.cssText = "display:flex;align-items:flex-start;gap:10px;border:1px solid #e2e8f0;border-radius:14px;padding:12px;cursor:pointer;background:#fff";
        const radio = node("input");
        radio.type = "radio";
        radio.name = "reviewed-product-sort-spec";
        radio.value = key;
        radio.checked = selected === key;
        radio.style.cssText = "margin-top:3px;width:17px;height:17px;accent-color:#6d28d9";
        radio.onchange = () => { selected = key; };
        const content = node("div");
        content.style.cssText = "min-width:0;flex:1";
        const title = node("div", label);
        title.style.cssText = "font-size:14px;font-weight:950;color:#0f172a";
        content.appendChild(title);
        if (summary) {
            const small = node("div", summary);
            small.style.cssText = "margin-top:4px;font-size:11px;font-weight:700;color:#64748b;line-height:1.65";
            content.appendChild(small);
        }
        row.append(radio, content);
        return row;
    }

    choices.appendChild(choiceRow({
        key: "",
        label: "بدون ترتيب مخصص",
        summary: "يُستخدم الترتيب التشغيلي المعتاد حسب وقت المراجعة ورقم الطلب.",
    }));
    candidates.forEach((candidate) => {
        choices.appendChild(choiceRow({
            key: text(candidate?.key),
            label: text(candidate?.label || candidate?.key),
            summary: reviewedProductSortCandidateSummary(candidate, 5),
        }));
    });

    const error = node("div");
    error.style.cssText = "display:none;margin-top:12px;border-radius:10px;background:#fff1f2;padding:10px;color:#be123c;font-size:12px;font-weight:800";
    const actions = node("div");
    actions.style.cssText = "display:flex;gap:9px;margin-top:18px";
    const cancel = node("button", "إلغاء");
    cancel.type = "button";
    cancel.style.cssText = "min-height:46px;border:1px solid #cbd5e1;border-radius:12px;background:white;padding:0 18px;font-weight:900;color:#475569";
    cancel.onclick = () => closeModal(overlay);
    const save = node("button", "حفظ ترتيب الملف");
    save.type = "button";
    save.style.cssText = "min-height:46px;flex:1;border:0;border-radius:12px;background:#6d28d9;padding:0 18px;font-weight:950;color:white";
    save.onclick = async () => {
        save.disabled = true;
        save.textContent = "جارٍ الحفظ…";
        error.style.display = "none";
        try {
            const { data } = await api.put("/reviewed-product-sorting-v1/preference", {
                group_key: product.group_key,
                spec_key: selected || null,
            });
            const updated = updateReviewedProductSortPreference(product, data);
            products = products.map((row) =>
                row.group_key === updated.group_key ? updated : row,
            );
            document.querySelectorAll(`[${BUTTON_ATTR}]`).forEach((button) => {
                if (text(button.dataset.groupKey) === text(updated.group_key)) {
                    updateButton(button, updated);
                }
            });
            closeModal(overlay);
        } catch (saveError) {
            error.textContent = saveError?.response?.data?.detail?.message
                || saveError?.message
                || "تعذّر حفظ ترتيب المنتج.";
            error.style.display = "block";
            save.disabled = false;
            save.textContent = "حفظ ترتيب الملف";
        }
    };
    actions.append(cancel, save);

    panel.append(header, choices, error, actions);
    overlay.appendChild(panel);
    overlay.onclick = (event) => {
        if (event.target === overlay) closeModal(overlay);
    };
    document.body.appendChild(overlay);
}

function addButton(card, product) {
    let button = card.querySelector(`[${BUTTON_ATTR}]`);
    if (!button) {
        button = node("button");
        button.type = "button";
        button.setAttribute(BUTTON_ATTR, "1");
        button.style.cssText = "margin-top:8px;min-height:39px;width:100%;border:1px solid #c4b5fd;border-radius:11px;background:#f5f3ff;padding:7px 10px;color:#5b21b6;font-size:12px;font-weight:900";
        button.onclick = (event) => {
            event.preventDefault();
            event.stopPropagation();
            const current = products.find((row) => text(row.group_key) === text(button.dataset.groupKey));
            if (current && !button.disabled) openSortModal(current);
        };
        const footer = card.lastElementChild || card;
        footer.appendChild(button);
    }
    updateButton(button, product);
}

async function scan() {
    scanScheduled = false;
    const stage = document.querySelector('[data-testid="reviewed-orders-stage"]');
    if (!stage) return;
    try {
        await loadProducts();
    } catch (_error) {
        return;
    }
    const used = new Set();
    stage.querySelectorAll('[data-testid="reviewed-product-card"]').forEach((card) => {
        const product = findReviewedProductForCard(products, cardIdentity(card), used);
        if (!product) return;
        used.add(text(product.group_key));
        addButton(card, product);
    });
}

function scheduleScan() {
    if (scanScheduled) return;
    scanScheduled = true;
    window.requestAnimationFrame(scan);
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = node("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.appendChild(marker);

    const observer = new MutationObserver(scheduleScan);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("mezan:preparation-file-created", async () => {
        await loadProducts({ force: true }).catch(() => null);
        scheduleScan();
    });
    scheduleScan();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") {
        window.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
}
