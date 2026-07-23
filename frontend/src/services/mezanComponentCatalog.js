import {
    MEZAN_COST_RESOURCES_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_RECIPE_FIXTURES,
} from "../demo/mezanProductPreviewFixtures";
import {
    addMezanPreviewProductComponentLink,
    addMezanPreviewResource,
    getMezanProductWorkspace,
    removeMezanPreviewProductComponentLink,
    setMezanPreviewResourceCost,
} from "./mezanProductCatalog";

export const MEZAN_COMPONENT_PREVIEW_META = {
    mode: "isolated_preview",
    label: "بيانات مكونات تجريبية — لا تؤثر في سلة أو قيود أو المحاسبة",
    writes_enabled: false,
    inventory_writes_enabled: false,
    source: "mezan_component_fixture",
};

const COMPONENT_DETAILS = {
    "PKG-BAG": {
        family: "التغليف",
        category: "packaging",
        description: "كيس التغليف المستخدم مع المنتج قبل التسليم.",
        attributes: { material: "غير محدد", color: "غير محدد" },
    },
    "PKG-BOX": {
        family: "التغليف",
        category: "packaging",
        description: "علبة المنتج الأساسية قبل الشحن.",
        attributes: { material: "غير محدد", size: "غير محدد" },
    },
    "CHAIN-SILVER": {
        family: "السلاسل",
        category: "chains",
        description: "سلسال فضي مشترك يمكن ربطه بأكثر من منتج ووصفة.",
        attributes: { color: "فضي", finish: "غير محدد" },
    },
    "CHAIN-GOLD": {
        family: "السلاسل",
        category: "chains",
        description: "سلسال ذهبي مشترك يمكن ربطه بأكثر من منتج ووصفة.",
        attributes: { color: "ذهبي", finish: "غير محدد" },
    },
    "LABOR-ENGRAVING": {
        family: "أعمال التخصيص",
        category: "labor",
        description: "تكلفة تنفيذ النحت أو الاسم على المنتج.",
        attributes: { service_type: "نحت" },
    },
    "LABOR-PLATING": {
        family: "أعمال التشطيب",
        category: "finishing",
        description: "تكلفة خدمة الطلاء أو التشطيب.",
        attributes: { service_type: "طلاء" },
    },
    "LABOR-ASSEMBLY": {
        family: "أعمال التجهيز",
        category: "labor",
        description: "تكلفة تركيب وتجميع أجزاء المنتج.",
        attributes: { service_type: "تركيب" },
    },
};

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

function normalizeSearch(value) {
    return String(value || "")
        .toLowerCase()
        .normalize("NFKC")
        .replace(/[\u064B-\u0652\u0670\u0640]/g, "")
        .replace(/[أإآا]/g, "ا")
        .replace(/ة/g, "ه")
        .replace(/ى/g, "ي")
        .trim();
}

function componentFromResource(resource) {
    const details = COMPONENT_DETAILS[resource.code] || {};
    const unitCost = resource.unit_cost;
    const costKnown = unitCost !== null && unitCost !== undefined && unitCost !== "";

    return {
        id: resource.id,
        code: resource.code,
        name: resource.name,
        kind: resource.kind,
        unit: resource.unit,
        track_inventory: Boolean(resource.track_inventory),
        family: details.family || "غير مصنف",
        category: details.category || "other",
        description: details.description || "مكوّن تجريبي في كتالوج Mezan OS.",
        attributes: details.attributes || {},
        status: "draft_preview",
        source: resource.source || "manual_mezan_catalog",
        is_fixture: true,
        reference_cost: {
            amount: costKnown ? Number(unitCost) : null,
            currency: "SAR",
            source: costKnown ? "manual_preview" : "not_defined",
        },
    };
}

function productUsages(recipes, products) {
    const productsById = new Map((products || []).map((product) => [product.id, product]));
    const usages = new Map();

    for (const recipe of recipes || []) {
        const product = productsById.get(recipe.product_id);
        const addUsage = ({
            resourceId,
            source,
            reason,
            quantity,
            condition = null,
        }) => {
            if (!resourceId) return;
            const rows = usages.get(resourceId) || [];
            rows.push({
                id: [
                    recipe.id,
                    resourceId,
                    source,
                    condition?.option_key || "always",
                    condition?.value_key || "always",
                ].join(":"),
                recipe_id: recipe.id,
                recipe_version: recipe.version,
                product_id: product?.id || recipe.product_id,
                product_name: product?.name || "منتج غير متوفر",
                product_sku: product?.sku || "—",
                source,
                reason,
                quantity: Number(quantity || 0),
                condition,
            });
            usages.set(resourceId, rows);
        };

        for (const line of recipe.base_lines || []) {
            addUsage({
                resourceId: line.resource_id,
                source: "base",
                reason: line.reason || "مكوّن أساسي",
                quantity: line.quantity,
            });
        }
        for (const rule of recipe.option_rules || []) {
            for (const effect of rule.effects || []) {
                if (effect.type === "add_component") {
                    const option = (product?.options || []).find((entry) => (
                        entry.key === rule.when?.option_key
                    ));
                    const value = (option?.values || []).find((entry) => (
                        entry.key === rule.when?.value_key
                    ));
                    addUsage({
                        resourceId: effect.resource_id,
                        source: "option",
                        reason: `خيار ${option?.name || rule.when?.option_key || "—"} = ${value?.name || rule.when?.value_key || "—"}`,
                        quantity: effect.quantity,
                        condition: {
                            option_key: rule.when?.option_key || null,
                            value_key: rule.when?.value_key || null,
                            option_name: option?.name || null,
                            value_name: value?.name || null,
                        },
                    });
                }
            }
        }
    }

    return usages;
}

export function buildMezanComponentWorkspace({
    resources = MEZAN_COST_RESOURCES_FIXTURES,
    recipes = MEZAN_PRODUCT_RECIPE_FIXTURES,
    products = MEZAN_PRODUCT_PREVIEW_FIXTURES,
} = {}) {
    const usages = productUsages(recipes, products);
    const components = (resources || []).map((resource) => ({
        ...componentFromResource(resource),
        product_usages: usages.get(resource.id) || [],
    }));

    return {
        components,
        products: (products || []).map((product) => ({
            id: product.id,
            salla_id: product.salla_id,
            sku: product.sku,
            name: product.name,
            options: product.options || [],
            source: product.source,
        })),
        meta: clone(MEZAN_COMPONENT_PREVIEW_META),
    };
}

export async function getMezanComponentWorkspace() {
    const workspace = await getMezanProductWorkspace();
    return clone(buildMezanComponentWorkspace({
        resources: workspace.resources,
        recipes: workspace.recipes,
        products: workspace.products,
    }));
}

export async function saveMezanComponentPreviewCost(componentId, amount) {
    const result = setMezanPreviewResourceCost(componentId, amount);
    return {
        ...result,
        component_workspace: result.ok ? await getMezanComponentWorkspace() : null,
    };
}

export async function addMezanComponentPreview(component) {
    const result = addMezanPreviewResource(component);
    return {
        ...result,
        component_workspace: result.ok ? await getMezanComponentWorkspace() : null,
    };
}

export async function linkMezanComponentToProductPreview(link) {
    const result = addMezanPreviewProductComponentLink(link);
    return {
        ...result,
        component_workspace: result.ok ? await getMezanComponentWorkspace() : null,
    };
}

export async function unlinkMezanComponentFromProductPreview(link) {
    const result = removeMezanPreviewProductComponentLink(link);
    return {
        ...result,
        component_workspace: result.ok ? await getMezanComponentWorkspace() : null,
    };
}

export function summarizeMezanComponents(components) {
    const rows = components || [];
    return {
        total: rows.length,
        stock_components: rows.filter((row) => row.track_inventory).length,
        labor_services: rows.filter((row) => !row.track_inventory).length,
        missing_cost: rows.filter((row) => row.reference_cost?.amount == null).length,
    };
}

export function filterMezanComponents(components, { query = "", filter = "all" } = {}) {
    const needle = normalizeSearch(query);
    return (components || []).filter((component) => {
        const matchesFilter = (
            filter === "all"
            || (filter === "stock" && component.track_inventory)
            || (filter === "service" && !component.track_inventory)
            || (filter === "missing_cost" && component.reference_cost?.amount == null)
        );
        if (!matchesFilter) return false;
        if (!needle) return true;
        return normalizeSearch([
            component.name,
            component.code,
            component.family,
            component.description,
        ].join(" ")).includes(needle);
    });
}
