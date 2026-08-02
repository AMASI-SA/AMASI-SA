import { listReviewedProductCatalog } from "./services/orderReviewEngine";

const ROOT_ID = "mezan-reviewed-product-sort-thumbnail-enhancer";
const MODAL_TITLE = "ترتيب بطاقات المنتجات في الملف";
const CARD_MARKER = "data-reviewed-sort-thumbnail-ready";

let products = [];
let loadingPromise = null;
let syncScheduled = false;

const text = (value) => String(value || "").trim();

export function reviewedProductThumbnailUrl(product = {}) {
    return text(
        product.image_url
        || product.selected_image_url
        || product.main_image
        || product.thumbnail_url
        || product.thumbnail
        || product.image,
    );
}

function modalPanel(root = document) {
    const title = [...root.querySelectorAll("h2")].find(
        (node) => text(node.textContent) === MODAL_TITLE,
    );
    return title?.closest("section") || null;
}

function placeholder() {
    const box = document.createElement("span");
    box.textContent = "بدون صورة";
    box.style.cssText = "display:flex;width:54px;height:54px;flex:none;align-items:center;justify-content:center;border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc;padding:4px;text-align:center;color:#94a3b8;font-size:9px;font-weight:850;line-height:1.25";
    return box;
}

function thumbnail(product) {
    const frame = document.createElement("span");
    frame.style.cssText = "display:flex;width:54px;height:54px;flex:none;overflow:hidden;align-items:center;justify-content:center;border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc";

    const url = reviewedProductThumbnailUrl(product);
    if (!url) {
        frame.append(placeholder());
        return frame;
    }

    const image = document.createElement("img");
    image.src = url;
    image.alt = text(product.name) || "صورة المنتج";
    image.loading = "lazy";
    image.style.cssText = "width:100%;height:100%;object-fit:cover";
    image.onerror = () => {
        frame.replaceChildren(placeholder());
    };
    frame.append(image);
    return frame;
}

function productForCard(card, index, used) {
    const name = text(card.querySelector("strong")?.textContent);
    const exact = products.find((product) => (
        !used.has(text(product.group_key))
        && text(product.name) === name
    ));
    if (exact) return exact;
    return products[index] || null;
}

function decorateCard(card, product) {
    if (!card || !product || card.hasAttribute(CARD_MARKER)) return;
    const heading = card.firstElementChild;
    const name = heading?.querySelector("strong");
    if (!heading || !name) return;

    const identity = document.createElement("div");
    identity.style.cssText = "display:flex;min-width:0;flex:1;align-items:center;gap:10px";
    name.style.minWidth = "0";
    heading.insertBefore(identity, name);
    identity.append(thumbnail(product), name);
    card.setAttribute(CARD_MARKER, "1");
}

function decorateModal() {
    const panel = modalPanel();
    if (!panel || !products.length) return;
    const cards = [...panel.querySelectorAll("article")];
    const used = new Set();
    cards.forEach((card, index) => {
        const product = productForCard(card, index, used);
        if (product) used.add(text(product.group_key));
        decorateCard(card, product);
    });
}

async function loadProducts() {
    if (!loadingPromise) {
        loadingPromise = listReviewedProductCatalog({ limit: 2000 })
            .then((catalog) => {
                products = Array.isArray(catalog?.products) ? catalog.products : [];
                return products;
            })
            .catch((error) => {
                loadingPromise = null;
                throw error;
            });
    }
    return loadingPromise;
}

function scheduleSync() {
    if (syncScheduled) return;
    syncScheduled = true;
    window.requestAnimationFrame(async () => {
        syncScheduled = false;
        if (!modalPanel()) return;
        try {
            await loadProducts();
            decorateModal();
        } catch (_error) {
            // The sort manager remains fully usable even if thumbnails fail.
        }
    });
}

function start() {
    if (!document.body || document.getElementById(ROOT_ID)) return;
    const marker = document.createElement("div");
    marker.id = ROOT_ID;
    marker.hidden = true;
    document.body.append(marker);

    const observer = new MutationObserver(scheduleSync);
    observer.observe(document.body, { childList: true, subtree: true });
    scheduleSync();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") {
        window.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
}
