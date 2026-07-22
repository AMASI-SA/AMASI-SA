import {
    MEZAN_COST_RESOURCES_FIXTURES,
    MEZAN_ORDER_LINE_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_META,
    MEZAN_PRODUCT_RECIPE_FIXTURES,
    MEZAN_READY_CONFIGURATION_STOCK_FIXTURES,
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
export async function getMezanProductWorkspace() {
    return clone({
        products: MEZAN_PRODUCT_PREVIEW_FIXTURES,
        resources: MEZAN_COST_RESOURCES_FIXTURES,
        recipes: MEZAN_PRODUCT_RECIPE_FIXTURES,
        ready_stock: MEZAN_READY_CONFIGURATION_STOCK_FIXTURES,
        order_examples: MEZAN_ORDER_LINE_PREVIEW_FIXTURES,
        meta: MEZAN_PRODUCT_PREVIEW_META,
    });
}

export async function listMezanProducts() {
    const workspace = await getMezanProductWorkspace();
    return { items: workspace.products, meta: workspace.meta };
}
