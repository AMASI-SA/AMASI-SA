import {
    MEZAN_COST_RESOURCES_FIXTURES,
    MEZAN_ORDER_LINE_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_META,
    MEZAN_PRODUCT_RECIPE_FIXTURES,
    MEZAN_INVENTORY_CONFIGURATION_FIXTURES,
    MEZAN_INVENTORY_MOVEMENT_FIXTURES,
    MEZAN_INVENTORY_RESERVATION_FIXTURES,
    MEZAN_STORAGE_LOCATION_FIXTURES,
} from "../demo/mezanProductPreviewFixtures";

export const MEZAN_PRODUCT_PAGE_POLICY = Object.freeze({
    page_id: "products-v2",
    mode: "read_only_aggregate",
    writes_enabled: false,
    operational_sources: [
        {
            id: "components",
            title: "المكونات والتكاليف",
            page: "صفحة المكونات",
            description: "إضافة المكوّن، تكلفة الوحدة، والكمية المخزنية تتم في صفحة المكونات المستقلة.",
        },
        {
            id: "purchase_invoices",
            title: "شراء المخزون",
            page: "صفحة فواتير المشتريات",
            description: "الاستلام والتكلفة والدفعة وموقع التخزين تأتي من فاتورة الشراء وقت اعتمادها.",
        },
        {
            id: "production_orders",
            title: "التخصيص والتصنيع",
            page: "صفحة أوامر الإنتاج",
            description: "استهلاك المخزون العام وإنشاء مخزون مطابق للمواصفات يتم في أمر إنتاج مستقل.",
        },
        {
            id: "returns",
            title: "المرتجعات",
            page: "صفحة المرتجعات",
            description: "المراجعة والموافقة وإرجاع القطعة إلى موقعها المخزني تتم في صفحة المرتجعات.",
        },
    ],
});

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
        inventory_configurations: MEZAN_INVENTORY_CONFIGURATION_FIXTURES,
        inventory_locations: MEZAN_STORAGE_LOCATION_FIXTURES,
        inventory_movements: MEZAN_INVENTORY_MOVEMENT_FIXTURES,
        inventory_reservations: MEZAN_INVENTORY_RESERVATION_FIXTURES,
        order_examples: MEZAN_ORDER_LINE_PREVIEW_FIXTURES,
        meta: MEZAN_PRODUCT_PREVIEW_META,
        page_policy: MEZAN_PRODUCT_PAGE_POLICY,
    });
}

export async function listMezanProducts() {
    const workspace = await getMezanProductWorkspace();
    return { items: workspace.products, meta: workspace.meta };
}
