export const REVIEWED_PRODUCTS_SECONDARY_TARGET = "/fulfillment-v2?stage=reviewed&view=products";

let scheduled = false;

export function removeReviewedProductsSecondaryTab(root = document) {
    const navigation = root.querySelector?.('[data-testid="mezan-v2-secondary-fulfillment"]');
    if (!navigation) return 0;

    let removed = 0;
    navigation.querySelectorAll('a[href]').forEach((link) => {
        const target = String(link.getAttribute("href") || "").trim();
        if (target !== REVIEWED_PRODUCTS_SECONDARY_TARGET) return;
        link.remove();
        removed += 1;
    });
    return removed;
}

function scheduleRemoval() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
        scheduled = false;
        removeReviewedProductsSecondaryTab();
    });
}

function start() {
    if (!document.body) return;
    removeReviewedProductsSecondaryTab();
    const observer = new MutationObserver(scheduleRemoval);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("popstate", scheduleRemoval);
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
    if (document.readyState === "loading") {
        window.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
}
