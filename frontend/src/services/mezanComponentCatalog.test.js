import {
    buildMezanComponentWorkspace,
    filterMezanComponents,
    getMezanComponentWorkspace,
    linkMezanComponentToProductPreview,
    summarizeMezanComponents,
    unlinkMezanComponentFromProductPreview,
} from "./mezanComponentCatalog";
import {
    getMezanProductWorkspace,
    resetMezanProductWorkspacePreview,
} from "./mezanProductCatalog";

describe("Mezan component preview catalog", () => {
    beforeEach(() => {
        resetMezanProductWorkspacePreview();
    });

    test("builds the seven isolated component definitions without stock quantities", () => {
        const workspace = buildMezanComponentWorkspace();

        expect(workspace.components).toHaveLength(7);
        expect(workspace.products).toHaveLength(1);
        expect(workspace.products[0].sku).toBe("AMS10026");
        expect(workspace.meta.writes_enabled).toBe(false);
        expect(workspace.meta.inventory_writes_enabled).toBe(false);
        for (const component of workspace.components) {
            expect(component).not.toHaveProperty("stock_quantity");
            expect(component).not.toHaveProperty("quantity");
        }
    });

    test("summarizes stock components, services, and missing costs", () => {
        const { components } = buildMezanComponentWorkspace();

        expect(summarizeMezanComponents(components)).toEqual({
            total: 7,
            active: 7,
            inactive: 0,
            stock_components: 4,
            labor_services: 3,
            missing_cost: 7,
        });
    });

    test("supports Arabic search and operational filters", () => {
        const { components } = buildMezanComponentWorkspace();

        expect(filterMezanComponents(components, { query: "سلسال" })).toHaveLength(2);
        expect(filterMezanComponents(components, { filter: "stock" })).toHaveLength(4);
        expect(filterMezanComponents(components, { filter: "service" })).toHaveLength(3);
        expect(filterMezanComponents(components, { filter: "missing_cost" })).toHaveLength(7);
    });

    test("hides stopped components by default and exposes explicit status filters", () => {
        const { components } = buildMezanComponentWorkspace();
        const rows = components.map((component, index) => (
            index === 0 ? { ...component, status: "inactive" } : component
        ));

        expect(filterMezanComponents(rows)).toHaveLength(6);
        expect(filterMezanComponents(rows, { status: "inactive" })).toEqual([
            expect.objectContaining({ id: rows[0].id, status: "inactive" }),
        ]);
        expect(filterMezanComponents(rows, { status: "all" })).toHaveLength(7);
        expect(summarizeMezanComponents(rows)).toMatchObject({ active: 6, inactive: 1 });
    });

    test("derives component usages from the product recipe", () => {
        const { components } = buildMezanComponentWorkspace();
        const bag = components.find((component) => component.code === "PKG-BAG");
        const plating = components.find((component) => component.code === "LABOR-PLATING");

        expect(bag.product_usages).toHaveLength(1);
        expect(bag.product_usages[0].product_sku).toBe("AMS10026");
        expect(bag.product_usages[0].quantity).toBe(1);
        expect(bag.product_usages[0].condition).toBeNull();
        expect(plating.product_usages).toHaveLength(0);
    });

    test("keeps option-specific component links explicit", () => {
        const { components } = buildMezanComponentWorkspace();
        const silverChain = components.find((component) => component.code === "CHAIN-SILVER");

        expect(silverChain.product_usages).toHaveLength(1);
        expect(silverChain.product_usages[0]).toMatchObject({
            product_sku: "AMS10026",
            source: "option",
            quantity: 1,
            condition: {
                option_key: "color",
                value_key: "silver",
            },
        });
    });

    test("writes a preview link to the canonical product recipe and derives it back", async () => {
        const initial = await getMezanComponentWorkspace();
        const plating = initial.components.find((component) => component.code === "LABOR-PLATING");
        const product = initial.products[0];

        const linked = await linkMezanComponentToProductPreview({
            productId: product.id,
            resourceId: plating.id,
            quantity: 1,
            condition: null,
        });

        expect(linked.ok).toBe(true);
        const productWorkspace = await getMezanProductWorkspace();
        const recipe = productWorkspace.recipes.find((entry) => entry.product_id === product.id);
        expect(recipe.base_lines).toContainEqual(expect.objectContaining({
            resource_id: plating.id,
            quantity: 1,
        }));
        const componentWorkspace = await getMezanComponentWorkspace();
        expect(componentWorkspace.components
            .find((component) => component.id === plating.id)
            .product_usages).toContainEqual(expect.objectContaining({
                product_id: product.id,
                quantity: 1,
                condition: null,
            }));

        const removed = await unlinkMezanComponentFromProductPreview({
            productId: product.id,
            resourceId: plating.id,
            condition: null,
        });
        expect(removed.ok).toBe(true);
        const afterRemoval = await getMezanProductWorkspace();
        expect(afterRemoval.recipes
            .find((entry) => entry.product_id === product.id)
            .base_lines
            .some((line) => line.resource_id === plating.id)).toBe(false);
    });

    test("rejects option links that are not present on the selected product", async () => {
        const workspace = await getMezanComponentWorkspace();
        const plating = workspace.components.find((component) => component.code === "LABOR-PLATING");

        const result = await linkMezanComponentToProductPreview({
            productId: workspace.products[0].id,
            resourceId: plating.id,
            quantity: 1,
            condition: { option_key: "size", value_key: "large" },
        });

        expect(result).toMatchObject({ ok: false, code: "option_value_not_found" });
    });

    test("adds and removes a valid option-specific product link", async () => {
        const workspace = await getMezanComponentWorkspace();
        const plating = workspace.components.find((component) => component.code === "LABOR-PLATING");
        const product = workspace.products[0];
        const condition = { option_key: "color", value_key: "silver" };

        const linked = await linkMezanComponentToProductPreview({
            productId: product.id,
            resourceId: plating.id,
            quantity: 1,
            condition,
        });
        expect(linked.ok).toBe(true);
        const usage = linked.component_workspace.components
            .find((component) => component.id === plating.id)
            .product_usages[0];
        expect(usage).toMatchObject({
            product_id: product.id,
            quantity: 1,
            condition: {
                option_key: "color",
                value_key: "silver",
                option_name: "اللون",
                value_name: "فضي",
            },
        });

        const removed = await unlinkMezanComponentFromProductPreview({
            productId: product.id,
            resourceId: plating.id,
            condition,
        });
        expect(removed.ok).toBe(true);
        expect(removed.component_workspace.components
            .find((component) => component.id === plating.id)
            .product_usages).toHaveLength(0);
    });

    test("supports the same component across multiple product recipes", () => {
        const base = buildMezanComponentWorkspace();
        const secondProduct = {
            ...base.products[0],
            id: "product-second",
            sku: "AMS-SECOND",
            name: "منتج ثانٍ",
        };
        const secondRecipe = {
            id: "recipe-second",
            product_id: secondProduct.id,
            version: 1,
            base_lines: [{ resource_id: "component-packaging-bag", quantity: 2 }],
            option_rules: [],
        };
        const workspace = buildMezanComponentWorkspace({
            products: [...base.products, secondProduct],
            recipes: [
                {
                    id: "recipe-first",
                    product_id: base.products[0].id,
                    version: 1,
                    base_lines: [{ resource_id: "component-packaging-bag", quantity: 1 }],
                    option_rules: [],
                },
                secondRecipe,
            ],
        });
        const bag = workspace.components.find((component) => component.code === "PKG-BAG");

        expect(bag.product_usages.map((usage) => usage.product_sku)).toEqual([
            "AMS10026",
            "AMS-SECOND",
        ]);
        expect(bag.product_usages.map((usage) => usage.quantity)).toEqual([1, 2]);
    });

    test("keeps usage identities unique when two option fields share a value key", () => {
        const base = buildMezanComponentWorkspace();
        const product = {
            ...base.products[0],
            options: [
                { key: "engraving", name: "النحت", values: [{ key: "yes", name: "نعم" }] },
                { key: "gift_wrap", name: "التغليف", values: [{ key: "yes", name: "نعم" }] },
            ],
        };
        const recipe = {
            id: "recipe-shared-value",
            product_id: product.id,
            version: 1,
            base_lines: [],
            option_rules: [
                {
                    id: "engraving-yes",
                    when: { option_key: "engraving", value_key: "yes" },
                    effects: [{ type: "add_component", resource_id: "service-plating", quantity: 1 }],
                },
                {
                    id: "gift-wrap-yes",
                    when: { option_key: "gift_wrap", value_key: "yes" },
                    effects: [{ type: "add_component", resource_id: "service-plating", quantity: 1 }],
                },
            ],
        };
        const workspace = buildMezanComponentWorkspace({
            products: [product],
            recipes: [recipe],
        });
        const usages = workspace.components
            .find((component) => component.code === "LABOR-PLATING")
            .product_usages;

        expect(usages).toHaveLength(2);
        expect(new Set(usages.map((usage) => usage.id)).size).toBe(2);
        expect(usages.map((usage) => usage.id)).toEqual(expect.arrayContaining([
            expect.stringContaining("engraving:yes"),
            expect.stringContaining("gift_wrap:yes"),
        ]));
    });
});
