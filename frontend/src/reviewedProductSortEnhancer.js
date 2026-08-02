import api from "./lib/api";
import { listReviewedProductCatalog } from "./services/orderReviewEngine";
import {
    findReviewedProductForCard,
    reviewedProductSortButtonLabel,
    reviewedProductSortCandidateSummary,
    updateReviewedProductSortPreference,
} from "./reviewedProductSortUi";

const ROOT_ID = "mezan-reviewed-product-sort-enhancer";
const BUTTON_ID = "mezan-reviewed-product-sort-button";
const STYLE_ID = "mezan-reviewed-product-sort-badge-style";
let products = [];
let modal = null;
let loadingPromise = null;

const text = (value) => String(value || "").trim();

function element(tag, value = "") {
    const node = document.createElement(tag);
    if (value) node.textContent = value;
    return node;
}

function productThumbnailUrl(product) {
    return text(
        product?.image_url
        || product?.selected_image_url
        || product?.main_image
        || product?.image,
    );
}

function productThumbnail(product) {
    const frame = element("div");
    frame.setAttribute("data-testid", "reviewed-sort-product-thumbnail");
    frame.style.cssText = "display:flex;width:54px;height:54px;flex:none;align-items:center;justify-content:center;overflow:hidden;border:1px solid #e2e8f0;border-radius:13px;background:#f8fafc;color:#94a3b8;font-size:10px;font-weight:850";

    const url = productThumbnailUrl(product);
    if (!url) {
        frame.textContent = "بدون صورة";
        return frame;
    }

    const image = element("img");
    image.src = url;
    image.alt = text(product?.name) || "صورة المنتج";
    image.loading = "lazy";
    image.style.cssText = "display:block;width:100%;height:100%;object-fit:cover";
    image.onerror = () => {
        image.remove();
        frame.textContent = "بدون صورة";
    };
    frame.append(image);
    return frame;
}

function isReviewedStage() {
    const params = new URLSearchParams(window.location.search);
    return (
        (window.location.pathname.includes("/fulfillment-v2") && params.get("stage") === "reviewed")
        || Boolean(document.querySelector('[data-testid="reviewed-orders-stage"]'))
    );
}

function ensureBadgeStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = element("style");
    style.id = STYLE_ID;
    style.textContent = `
        [data-testid="reviewed-product-card"][data-reviewed-sort-label] {
            position: relative;
        }
        [data-testid="reviewed-product-card"][data-reviewed-sort-label]::after {
            content: attr(data-reviewed-sort-label);
            position: absolute;
            left: 12px;
            top: 12px;
            z-index: 5;
            max-width: 46%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            border: 1px solid #c4b5fd;
            border-radius: 999px;
            background: #f5f3ff;
            padding: 5px 10px;
            color: #6d28d9;
            font-family: Tajawal, sans-serif;
            font-size: 11px;
            font-weight: 900;
            box-shadow: 0 5px 16px #6d28d91f;
        }
        @media (max-width: 640px) {
            [data-testid="reviewed-product-card"][data-reviewed-sort-label]::after {
                left: 8px;
                top: 8px;
                max-width: 52%;
                padding: 4px 8px;
                font-size: 10px;
            }
        }
    `;
    document.head.append(style);
}

function cardIdentity(card) {
    const name = text(card.querySelector("h3")?.textContent);
    const skuNode = [...card.querySelectorAll('[dir="ltr"]')].find((entry) =>
        text(entry.textContent).toUpperCase().startsWith("SKU:"),
    );
    const sku = text(skuNode?.textContent).replace(/^SKU:\s*/i, "");
    return { name, sku };
}

function syncProductBadges() {
    ensureBadgeStyles();
    const stage = document.querySelector('[data-testid="reviewed-orders-stage"]');
    if (!stage) return;

    const used = new Set();
    stage.querySelectorAll('[data-testid="reviewed-product-card"]').forEach((card) => {
        const product = findReviewedProductForCard(products, cardIdentity(card), used);
        if (!product) {
            card.removeAttribute("data-reviewed-sort-label");
            return;
        }
        used.add(text(product.group_key));
        const label = text(product.preparation_sort_label || product.preparation_sort_spec);
        if (label) {
            card.setAttribute("data-reviewed-sort-label", `ترتيب الملف: ${label}`);
        } else {
            card.removeAttribute("data-reviewed-sort-label");
        }
    });
}

async function loadProducts(force = false) {
    if (force) loadingPromise = null;
    if (!loadingPromise) {
        loadingPromise = listReviewedProductCatalog({ limit: 2000 })
            .then((catalog) => {
                products = Array.isArray(catalog?.products) ? catalog.products : [];
                window.setTimeout(syncProductBadges, 0);
                return products;
            })
            .catch((error) => {
                loadingPromise = null;
                throw error;
            });
    }
    return loadingPromise;
}

function closeModal() {
    modal?.remove();
    modal = null;
}

function candidateFor(product, key) {
    return (product?.preparation_sort_candidates || []).find(
        (candidate) => text(candidate?.key) === text(key),
    ) || null;
}

function productRow(product) {
    const card = element("article");
    card.style.cssText = "border:1px solid #e2e8f0;border-radius:16px;background:#fff;padding:14px";

    const heading = element("div");
    heading.style.cssText = "display:flex;align-items:flex-start;justify-content:space-between;gap:10px";

    const identity = element("div");
    identity.style.cssText = "display:flex;min-width:0;flex:1;align-items:flex-start;gap:10px";
    const thumbnail = productThumbnail(product);
    const nameBox = element("div");
    nameBox.style.cssText = "min-width:0;flex:1";
    const name = element("div", text(product?.name) || "منتج");
    name.style.cssText = "font-size:14px;font-weight:950;color:#0f172a;line-height:1.6";
    const sku = element("div", text(product?.sku) ? `SKU: ${text(product.sku)}` : "");
    sku.style.cssText = "margin-top:2px;font-size:10px;font-weight:750;color:#94a3b8;direction:ltr;text-align:right";
    nameBox.append(name);
    if (sku.textContent) nameBox.append(sku);
    identity.append(thumbnail, nameBox);

    const quantity = Math.max(0, Math.floor(Number(product?.remaining_quantity ?? product?.quantity) || 0));
    const badge = element("div", `${quantity} قطعة`);
    badge.style.cssText = "flex:none;border-radius:999px;background:#ecfdf5;padding:6px 10px;color:#047857;font-size:11px;font-weight:950";
    heading.append(identity, badge);

    const current = element("div", reviewedProductSortButtonLabel(product));
    current.style.cssText = "margin-top:9px;font-size:11px;font-weight:850;color:#6d28d9";

    const candidates = Array.isArray(product?.preparation_sort_candidates)
        ? product.preparation_sort_candidates
        : [];
    const controls = element("div");
    controls.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;margin-top:10px";

    const select = element("select");
    select.style.cssText = "min-width:0;min-height:44px;border:1px solid #cbd5e1;border-radius:11px;background:#fff;padding:0 10px;font-size:12px;font-weight:850;color:#334155;outline:none";
    const noSort = element("option", "بدون ترتيب مخصص");
    noSort.value = "";
    select.append(noSort);
    candidates.forEach((candidate) => {
        const option = element("option", text(candidate?.label || candidate?.key));
        option.value = text(candidate?.key);
        select.append(option);
    });
    select.value = text(product?.preparation_sort_spec);

    const save = element("button", "حفظ");
    save.type = "button";
    save.style.cssText = "min-height:44px;border:0;border-radius:11px;background:#6d28d9;padding:0 17px;color:#fff;font-size:12px;font-weight:950";

    const summary = element("div");
    summary.style.cssText = "margin-top:8px;border-radius:10px;background:#f8fafc;padding:9px;color:#64748b;font-size:10px;font-weight:750;line-height:1.7";
    const status = element("div");
    status.style.cssText = "display:none;margin-top:8px;border-radius:10px;padding:9px;font-size:11px;font-weight:850";

    function refreshSummary() {
        const candidate = candidateFor(product, select.value);
        summary.textContent = candidate
            ? reviewedProductSortCandidateSummary(candidate, 8)
            : (candidates.length
                ? "اختر العمر أو المقاس أو اللون. القيمة الأكثر عددًا بالقطع ستظهر أولًا في ملف PDF."
                : "لا توجد مواصفات قابلة للترتيب في الطلبات الحالية لهذا المنتج.");
    }

    select.onchange = refreshSummary;
    refreshSummary();

    save.onclick = async () => {
        save.disabled = true;
        select.disabled = true;
        save.textContent = "جارٍ الحفظ…";
        status.style.display = "none";
        try {
            const { data } = await api.put("/reviewed-product-sorting-v1/preference", {
                group_key: product.group_key,
                spec_key: select.value || null,
            });
            const updated = updateReviewedProductSortPreference(product, data);
            products = products.map((row) => row.group_key === updated.group_key ? updated : row);
            product = updated;
            current.textContent = reviewedProductSortButtonLabel(updated);
            status.textContent = select.value
                ? "تم حفظ طريقة ترتيب بطاقات هذا المنتج."
                : "تم إلغاء الترتيب المخصص.";
            status.style.cssText = "display:block;margin-top:8px;border-radius:10px;padding:9px;background:#ecfdf5;color:#047857;font-size:11px;font-weight:850";
            refreshSummary();
            syncProductBadges();
        } catch (error) {
            status.textContent = error?.response?.data?.detail?.message
                || error?.message
                || "تعذّر حفظ ترتيب المنتج.";
            status.style.cssText = "display:block;margin-top:8px;border-radius:10px;padding:9px;background:#fff1f2;color:#be123c;font-size:11px;font-weight:850";
        } finally {
            save.disabled = false;
            select.disabled = false;
            save.textContent = "حفظ";
        }
    };

    controls.append(select, save);
    card.append(heading, current, controls, summary, status);
    return card;
}

function renderRows(container) {
    container.replaceChildren();
    if (!products.length) {
        const empty = element("div", "لا توجد منتجات متبقية في مرحلة تمت المراجعة.");
        empty.style.cssText = "border:1px dashed #cbd5e1;border-radius:14px;padding:25px;text-align:center;color:#64748b;font-size:13px";
        container.append(empty);
        return;
    }
    products.forEach((product) => container.append(productRow(product)));
}

async function openModal() {
    if (modal) return;
    const overlay = element("div");
    modal = overlay;
    overlay.style.cssText = "position:fixed;inset:0;z-index:2147483646;background:#02061799;display:flex;align-items:center;justify-content:center;padding:14px;direction:rtl";
    overlay.onclick = (event) => { if (event.target === overlay) closeModal(); };

    const panel = element("section");
    panel.style.cssText = "display:flex;width:min(760px,100%);max-height:92vh;flex-direction:column;overflow:hidden;border-radius:22px;background:#f8fafc;box-shadow:0 28px 90px #0005";
    const header = element("header");
    header.style.cssText = "display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-bottom:1px solid #e2e8f0;background:#fff;padding:18px";
    const titleBox = element("div");
    const title = element("h2", "ترتيب بطاقات المنتجات في الملف");
    title.style.cssText = "margin:0;font-size:20px;font-weight:950;color:#0f172a";
    const description = element("p", "اختر مواصفة واحدة لكل منتج. يتم تجميع البطاقات المتشابهة، وتظهر القيمة ذات أكبر عدد من القطع أولًا.");
    description.style.cssText = "margin:6px 0 0;color:#64748b;font-size:11px;line-height:1.8";
    titleBox.append(title, description);

    const actions = element("div");
    actions.style.cssText = "display:flex;gap:7px";
    const refresh = element("button", "تحديث");
    refresh.type = "button";
    refresh.style.cssText = "min-height:38px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;padding:0 12px;color:#475569;font-size:11px;font-weight:900";
    const close = element("button", "×");
    close.type = "button";
    close.style.cssText = "width:38px;height:38px;border:0;border-radius:10px;background:#f1f5f9;color:#475569;font-size:24px;line-height:1";
    close.onclick = closeModal;
    actions.append(refresh, close);
    header.append(titleBox, actions);

    const list = element("div");
    list.style.cssText = "display:grid;gap:10px;overflow:auto;padding:14px";
    const loading = element("div", "جارٍ تحميل المنتجات والمواصفات…");
    loading.style.cssText = "padding:30px;text-align:center;color:#64748b;font-size:13px;font-weight:850";
    list.append(loading);

    refresh.onclick = async () => {
        refresh.disabled = true;
        refresh.textContent = "…";
        try {
            await loadProducts(true);
            renderRows(list);
        } finally {
            refresh.disabled = false;
            refresh.textContent = "تحديث";
        }
    };

    panel.append(header, list);
    overlay.append(panel);
    document.body.append(overlay);

    try {
        await loadProducts(true);
        if (modal === overlay) renderRows(list);
    } catch (error) {
        if (modal !== overlay) return;
        list.replaceChildren();
        const warning = element("div", error?.message || "تعذّر تحميل المنتجات.");
        warning.style.cssText = "border-radius:14px;background:#fff1f2;padding:18px;color:#be123c;font-size:12px;font-weight:850";
        list.append(warning);
    }
}

function ensureButton() {
    let button = document.getElementById(BUTTON_ID);
    if (button) return button;
    button = element("button", "ترتيب بطاقات المنتجات");
    button.id = BUTTON_ID;
    button.type = "button";
    button.style.cssText = "position:fixed;right:22px;bottom:22px;z-index:2147483000;min-height:48px;border:1px solid #c4b5fd;border-radius:15px;background:#6d28d9;padding:0 18px;color:#fff;font-size:13px;font-weight:950;box-shadow:0 14px 40px #5b21b655";
    button.onclick = openModal;
    document.body.append(button);
    return button;
}

function syncVisibility() {
    const reviewed = isReviewedStage();
    ensureButton().style.display = reviewed ? "block" : "none";
    if (reviewed) {
        loadProducts().then(syncProductBadges).catch(() => null);
    }
}

function patchHistory() {
    ["pushState", "replaceState"].forEach((method) => {
        const original = window.history[method];
        if (original.__mezanSortPatched) return;
        const wrapped = function patchedHistory(...args) {
            const result = original.apply(this, args);
            window.setTimeout(syncVisibility, 0);
            return result;
        };
        wrapped.__mezanSortPatched = true;
        window.history[method] = wrapped;
    });
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = element("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.append(marker);
    ensureBadgeStyles();
    ensureButton();
    patchHistory();

    const observer = new MutationObserver(syncVisibility);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("popstate", syncVisibility);
    window.addEventListener("hashchange", syncVisibility);
    window.addEventListener("mezan:preparation-file-created", () => {
        loadingPromise = null;
        syncVisibility();
    });
    window.setInterval(syncVisibility, 1500);
    syncVisibility();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") {
        window.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
}
