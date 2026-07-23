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

const INITIAL_PREVIEW_WORKSPACE = {
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
};

// One in-memory source of truth for the Products and Components previews.
// It survives client-side navigation, but intentionally resets on a full reload.
let previewWorkspace = clone(INITIAL_PREVIEW_WORKSPACE);

function nextPreviewId(prefix, rows) {
    return `${prefix}-${Date.now()}-${(rows || []).length + 1}`;
}

function findProductOption(products, productId, condition) {
    const product = (products || []).find((entry) => entry.id === productId);
    if (!product || !condition) return { product, option: null, value: null };
    const option = (product.options || []).find((entry) => entry.key === condition.option_key);
    const value = (option?.values || []).find((entry) => entry.key === condition.value_key);
    return { product, option, value };
}

function ensureRecipe(workspace, productId) {
    const existing = (workspace.recipes || []).find((entry) => entry.product_id === productId);
    if (existing) return existing;
    const recipe = {
        id: nextPreviewId("recipe", workspace.recipes),
        product_id: productId,
        version: 1,
        status: "draft_preview",
        base_lines: [],
        option_rules: [],
    };
    workspace.recipes = [...(workspace.recipes || []), recipe];
    return recipe;
}

/**
 * Isolated adapter for the new Mezan OS product domain.
 *
 * While Salla products.read is pending, this adapter returns isolated virtual
 * fixtures only. Preview edits share one in-memory workspace between Products
 * and Components, but intentionally perform no HTTP, database, Salla, Qoyod,
 * inventory, or accounting writes. A full reload resets the preview.
 */
export async function getMezanProductWorkspace() {
    return clone(previewWorkspace);
}

export async function listMezanProducts() {
    const workspace = await getMezanProductWorkspace();
    return { items: workspace.products, meta: workspace.meta };
}

export function resetMezanProductWorkspacePreview() {
    previewWorkspace = clone(INITIAL_PREVIEW_WORKSPACE);
    return clone(previewWorkspace);
}

export function setMezanPreviewResourceCost(resourceId, rawAmount) {
    const amount = rawAmount === "" || rawAmount === null || rawAmount === undefined
        ? null
        : Number(rawAmount);
    if (amount !== null && (!Number.isFinite(amount) || amount < 0)) {
        return { ok: false, code: "invalid_cost" };
    }
    const exists = (previewWorkspace.resources || []).some((entry) => entry.id === resourceId);
    if (!exists) return { ok: false, code: "component_not_found" };
    previewWorkspace.resources = (previewWorkspace.resources || []).map((entry) => (
        entry.id === resourceId ? { ...entry, unit_cost: amount } : entry
    ));
    return { ok: true, workspace: clone(previewWorkspace) };
}

export function addMezanPreviewResource(resource) {
    const code = String(resource?.code || "").trim().toUpperCase();
    const name = String(resource?.name || "").trim();
    if (!code || !name) return { ok: false, code: "invalid_component" };
    if ((previewWorkspace.resources || []).some((entry) => entry.code === code)) {
        return { ok: false, code: "component_code_exists" };
    }
    const normalized = {
        id: resource.id || nextPreviewId("component", previewWorkspace.resources),
        code,
        name,
        kind: resource.kind === "labor_service" ? "labor_service" : "stock_component",
        unit: resource.kind === "labor_service" ? "job" : "piece",
        unit_cost: null,
        track_inventory: resource.kind !== "labor_service",
        source: "employee_preview",
    };
    previewWorkspace.resources = [...(previewWorkspace.resources || []), normalized];
    return { ok: true, resource: clone(normalized), workspace: clone(previewWorkspace) };
}

export function addMezanPreviewProductComponentLink({
    productId,
    resourceId,
    quantity,
    condition = null,
}) {
    const amount = Number(quantity);
    if (!Number.isFinite(amount) || amount <= 0) {
        return { ok: false, code: "invalid_quantity" };
    }
    const resourceExists = (previewWorkspace.resources || []).some((entry) => entry.id === resourceId);
    if (!resourceExists) return { ok: false, code: "component_not_found" };

    const { product, option, value } = findProductOption(
        previewWorkspace.products,
        productId,
        condition,
    );
    if (!product) return { ok: false, code: "product_not_found" };
    if (condition && (!option || !value)) {
        return { ok: false, code: "option_value_not_found" };
    }

    const recipe = ensureRecipe(previewWorkspace, productId);
    if (!condition) {
        if ((recipe.base_lines || []).some((line) => line.resource_id === resourceId)) {
            return { ok: false, code: "link_exists" };
        }
        recipe.base_lines = [
            ...(recipe.base_lines || []),
            {
                resource_id: resourceId,
                quantity: amount,
                reason: "مكوّن أساسي — ربط من كتالوج المكونات",
            },
        ];
    } else {
        let rule = (recipe.option_rules || []).find((entry) => (
            entry.when?.option_key === condition.option_key
            && entry.when?.value_key === condition.value_key
        ));
        if (!rule) {
            rule = {
                id: nextPreviewId("option-rule", recipe.option_rules),
                when: {
                    option_key: condition.option_key,
                    value_key: condition.value_key,
                },
                effects: [],
            };
            recipe.option_rules = [...(recipe.option_rules || []), rule];
        }
        if ((rule.effects || []).some((effect) => (
            effect.type === "add_component" && effect.resource_id === resourceId
        ))) {
            return { ok: false, code: "link_exists" };
        }
        rule.effects = [
            ...(rule.effects || []),
            { type: "add_component", resource_id: resourceId, quantity: amount },
        ];
    }

    return { ok: true, recipe: clone(recipe), workspace: clone(previewWorkspace) };
}

export function removeMezanPreviewProductComponentLink({
    productId,
    resourceId,
    condition = null,
}) {
    const recipe = (previewWorkspace.recipes || []).find((entry) => entry.product_id === productId);
    if (!recipe) return { ok: false, code: "recipe_not_found" };

    let removed = false;
    if (!condition) {
        const before = (recipe.base_lines || []).length;
        recipe.base_lines = (recipe.base_lines || []).filter((line) => line.resource_id !== resourceId);
        removed = recipe.base_lines.length !== before;
    } else {
        recipe.option_rules = (recipe.option_rules || []).map((rule) => {
            if (
                rule.when?.option_key !== condition.option_key
                || rule.when?.value_key !== condition.value_key
            ) return rule;
            const before = (rule.effects || []).length;
            const effects = (rule.effects || []).filter((effect) => !(
                effect.type === "add_component" && effect.resource_id === resourceId
            ));
            if (effects.length !== before) removed = true;
            return { ...rule, effects };
        }).filter((rule) => (rule.effects || []).length > 0);
    }

    return removed
        ? { ok: true, recipe: clone(recipe), workspace: clone(previewWorkspace) }
        : { ok: false, code: "link_not_found" };
}

export function replaceMezanPreviewRecipe(recipeId, nextRecipe) {
    const exists = (previewWorkspace.recipes || []).some((entry) => entry.id === recipeId);
    if (!exists) return { ok: false, code: "recipe_not_found" };
    previewWorkspace.recipes = (previewWorkspace.recipes || []).map((entry) => (
        entry.id === recipeId ? clone(nextRecipe) : entry
    ));
    return { ok: true, workspace: clone(previewWorkspace) };
}
