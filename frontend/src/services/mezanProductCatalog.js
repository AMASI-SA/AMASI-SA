import {
    MEZAN_PRODUCT_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_META,
} from "../demo/mezanProductPreviewFixtures";

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

/**
 * Read-only adapter for the new Mezan OS product domain.
 *
 * While Salla products.read is pending, this adapter returns isolated virtual
 * fixtures only. It intentionally performs no HTTP requests and exposes no
 * write operation. Once the scope is approved, the implementation can switch
 * to a dedicated Mezan OS products read endpoint without changing the page.
 */
export async function listMezanProducts() {
    return {
        items: clone(MEZAN_PRODUCT_PREVIEW_FIXTURES),
        meta: clone(MEZAN_PRODUCT_PREVIEW_META),
    };
}
