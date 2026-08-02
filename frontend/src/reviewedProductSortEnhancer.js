import api from "./lib/api";
import { listReviewedProductCatalog } from "./services/orderReviewEngine";
import {
    reviewedProductSortButtonLabel,
    reviewedProductSortCandidateSummary,
    updateReviewedProductSortPreference,
} from "./reviewedProductSortUi";

const ROOT_ID = "mezan-reviewed-product-sort-enhancer";
const LAUNCHER_ID = "mezan-reviewed-product-sort-launcher";
let products = [];
let loadingPromise = null;
let managerOverlay = null;
let visibilityScheduled = false;

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

function closeManager() {
    managerOverlay?.remove();
    managerOverlay = null;
}

function selectedCandidate(product, value) {
    const wanted = text(value);
    return (product?.preparation_sort_candidates || []).find(
        (candidate) => text(candidate?.key) === wanted,
    ) || null;
}

function renderProductRow(product) {
    const row = node("article");
    row.style.cssText = "border:1px solid #e2e8f0;border-radius:16px;background:#fff;padding:13px";

    const top = node("div");
    top.style.cssText = "display:flex;align-items:flex-start;justify-content:space-between;gap:10px";
    const identity = node("div");
    identity.style.cssText = "min-width:0;flex:1";
    const name = node("div", text(product?.name) || "منتج");
    name.style.cssText = "font-size:14px;font-weight:950;color:#0f172a;line-height:1.6";
    const sku = node("div", text(product?.sku) ? `SKU: ${text(product.sku)}` : "");
    sku.style.cssText = "margin-top:2px;font-size:10px;font-weight:750;color:#94a3b8;direction:ltr;text-align:right";
    identity.append(name);
    if (sku.textContent) identity.append(sku);

    const quantity = node(
        "div",
        `${Math.max(0, Math.floor(Number(product?.remaining_quantity ?? product?.quantity) || 0))} قطعة`,
    );
    quantity.style.cssText = "flex:none;border-radius:999px;background:#ecfdf5;padding:6px 10px;color:#047857;font-size:11px;font-weight:950";
    top.append(identity, quantity);

    const current = node("div", reviewedProductSortButtonLabel(product));
    current.style.cssText = "margin-top:9px;font-size:11px;font-weight:850;color:#6d28d9";

    const candidates = Array.isArray(product?.preparation_sort_candidates)
        ? product.preparation_sort_candidates
        : [];
    const controls = node("div");
    controls.style.cssText = "display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;margin-top:9px";

    const select = node("select");
    select.style.cssText = "min-width:0;min-height:43px;border:1px solid #cbd5e1;border-radius:11px;background:white;padding:0 10px;font-size:12px;font-weight:800;color:#334155;outline:none";
    const defaultOption = node("option", "بدون ترتيب مخصص");
    defaultOption.value = "";
    select.appendChild(defaultOption);
    candidates.forEach((candidate) => {
        const option = node("option", text(candidate?.label || candidate?.key));
        option.value = text(candidate?.key);
        select.appendChild(option);
    });
    select.value = text(product?.preparation_sort_spec);
    select.disabled = candidates.length === 0;

    const save = node("button", "حفظ");
    save.type = "button";
    save.disabled = candidates.length === 0;
    save.style.cssText = "min-height:43px;border:0;border-radius:11px;background:#6d28d9;padding:0 16px;color:white;font-size:12px;font-weight:950";

    const summary = node("div");
    summary.style.cssText = "margin-top:7px;border-radius:10px;background:#f8fafc;padding:8px 9px;color:#64748b;font-size:10px;font-weight:700;line-height:1.65";
    const status = node("div");
    status.style.cssText = "display:none;margin-top:7px;border-radius:10px;padding:8px 9px;font-size:11px;font-weight:850";

    function refreshSummary() {
        const candidate = selectedCandidate(product, select.value);
        summary.textContent = candidate
            ? reviewedProductSortCandidateSummary(candidate, 6)
            : (candidates.length
                ? "اختر مواصفة واحدة؛ القيمة صاحبة أكبر عدد من القطع ستظهر أولًا في الملف."
                : "لا توجد حاليًا مواصفات قابلة للترتيب في طلبات هذا المنتج.");
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
            products = products.map((item) =>
                item.group_key === updated.group_key ? updated : item,
            );
            product = updated;
            current.textContent = reviewedProductSortButtonLabel(updated);
            status.textContent = select.value
                ? "تم حفظ ترتيب بطاقات هذا المنتج."
                : "تم إلغاء الترتيب المخصص لهذا المنتج.";
            status.style.cssText = "display:block;margin-top:7px;border-radius:10px;padding:8px 9px;background:#ecfdf5;color:#047857;font-size:11px;font-weight:850";
            refreshSummary();
        } catch (error) {
            status.textContent = error?.response?.data?.detail?.message
                || error?.message
                || "تعذّر حفظ ترتيب المنتج.";
            status.style.cssText = "display:block;margin-top:7px;border-radius:10px;padding:8px 9px;background:#fff1f2;color:#be123c;font-size:11px;font-weight:850";
        } finally {
            save.disabled = candidates.length === 0;
            select.disabled = candidates.length === 0;
            save.textContent = "حفظ";
        }
    };

    controls.append(select, save);
    row.append(top, current, controls, summary, status);
    return row;
}

function renderManagerRows(container) {
    container.replaceChildren();
    if (!products.length) {
        const empty = node("div", "لا توجد منتجات متبقية في مرحلة تمت المراجعة.");
        empty.style.cssText = "border:1px dashed #cbd5e1;border-radius:14px;padding:24px;text-align:center;color:#64748b;font-size:13px";
        container.appendChild(empty);
        return;
    }
    products.forEach((product) => container.appendChild(renderProductRow(product)));
}

async function openManager() {
    if (managerOverlay) return;

    const overlay = node("div");
    managerOverlay = overlay;
    overlay.dataset.reviewedProductSortManager = "1";
    overlay.style.cssText = "position:fixed;inset:0;z-index:15000;background:#02061799;display:flex;align-items:center;justify-content:center;padding:14px;direction:rtl";
    overlay.onclick = (event) => {
        if (event.target === overlay) closeManager();
    };

    const panel = node("section");
    panel.style.cssText = "display:flex;width:min(720px,100%);max-height:92vh;flex-direction:column;overflow:hidden;border-radius:22px;background:#f8fafc;box-shadow:0 28px 90px #0005";

    const header = node("header");
    header.style.cssText = "display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-bottom:1px solid #e2e8f0;background:white;padding:18px";
    const headingBox = node("div");
    const heading = node("h2", "ترتيب بطاقات المنتجات في الملف");
    heading.style.cssText = "margin:0;font-size:20px;font-weight:950;color:#0f172a";
    const description = node(
        "p",
        "اختر مواصفة واحدة لكل منتج مثل العمر أو المقاس أو اللون. داخل PDF تتجمع البطاقات ذات القيمة المتشابهة، وتظهر القيمة الأكثر عددًا بالقطع أولًا.",
    );
    description.style.cssText = "margin:6px 0 0;color:#64748b;font-size:11px;line-height:1.8";
    headingBox.append(heading, description);

    const headerActions = node("div");
    headerActions.style.cssText = "display:flex;gap:7px";
    const refresh = node("button", "تحديث");
    refresh.type = "button";
    refresh.style.cssText = "min-height:37px;border:1px solid #cbd5e1;border-radius:10px;background:white;padding:0 12px;color:#475569;font-size:11px;font-weight:900";
    const close = node("button", "×");
    close.type = "button";
    close.style.cssText = "width:37px;height:37px;border:0;border-radius:10px;background:#f1f5f9;color:#475569;font-size:24px;line-height:1";
    close.onclick = closeManager;
    headerActions.append(refresh, close);
    header.append(headingBox, headerActions);

    const list = node("div");
    list.style.cssText = "display:grid;gap:9px;overflow:auto;padding:13px";
    const loading = node("div", "جارٍ تحميل المنتجات والمواصفات…");
    loading.style.cssText = "padding:28px;text-align:center;color:#64748b;font-size:13px;font-weight:800";
    list.appendChild(loading);

    refresh.onclick = async () => {
        refresh.disabled = true;
        refresh.textContent = "…";
        try {
            await loadProducts({ force: true });
            renderManagerRows(list);
        } finally {
            refresh.disabled = false;
            refresh.textContent = "تحديث";
        }
    };

    panel.append(header, list);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    try {
        await loadProducts({ force: true });
        if (managerOverlay === overlay) renderManagerRows(list);
    } catch (error) {
        if (managerOverlay !== overlay) return;
        list.replaceChildren();
        const warning = node("div", error?.message || "تعذّر تحميل المنتجات.");
        warning.style.cssText = "border-radius:14px;background:#fff1f2;padding:18px;color:#be123c;font-size:12px;font-weight:850";
        list.appendChild(warning);
    }
}

function launcher() {
    let button = document.getElementById(LAUNCHER_ID);
    if (button) return button;
    button = node("button", "ترتيب بطاقات المنتجات");
    button.id = LAUNCHER_ID;
    button.type = "button";
    button.hidden = true;
    button.style.cssText = "position:fixed;left:16px;top:92px;z-index:10500;min-height:46px;border:1px solid #c4b5fd;border-radius:14px;background:#6d28d9;padding:0 16px;color:white;font-size:12px;font-weight:950;box-shadow:0 14px 35px #5b21b644";
    button.onclick = openManager;
    document.body.appendChild(button);
    return button;
}

function syncVisibility() {
    visibilityScheduled = false;
    const button = launcher();
    button.hidden = !document.querySelector('[data-testid="reviewed-orders-stage"]');
}

function scheduleVisibility() {
    if (visibilityScheduled) return;
    visibilityScheduled = true;
    window.requestAnimationFrame(syncVisibility);
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = node("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.appendChild(marker);
    launcher();

    const observer = new MutationObserver(scheduleVisibility);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("mezan:preparation-file-created", () => {
        loadingPromise = null;
    });
    scheduleVisibility();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") {
        window.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
}
