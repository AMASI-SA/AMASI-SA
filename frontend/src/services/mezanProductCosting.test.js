import {
    MEZAN_COST_RESOURCES_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_RECIPE_FIXTURES,
    MEZAN_READY_CONFIGURATION_STOCK_FIXTURES,
} from "../demo/mezanProductPreviewFixtures";
import {
    buildConfigurationKey,
    calculateConfigurationCost,
    matchReadyConfigurationStock,
} from "./mezanProductCosting";

const product = MEZAN_PRODUCT_PREVIEW_FIXTURES[0];
const recipe = MEZAN_PRODUCT_RECIPE_FIXTURES[0];

test("normalizes configuration text before building the stock key", () => {
    expect(buildConfigurationKey({ sku: "ams10026", color: "silver", customerName: "  اسم تجريبي  " }))
        .toBe("AMS10026|color=silver|name=اسم تجريبي");
});

test("applies the silver internal cost delta exactly once", () => {
    const result = calculateConfigurationCost({
        recipe,
        resources: MEZAN_COST_RESOURCES_FIXTURES,
        selections: { color: "silver" },
    });
    const deltas = result.lines.filter((line) => line.type === "fixed_cost_delta");
    expect(deltas).toHaveLength(1);
    expect(deltas[0].total_cost).toBe(5);
    expect(result.known_total).toBe(5);
});

test("matches ready stock for the synthetic silver name and returns fifty", () => {
    const result = matchReadyConfigurationStock({
        readyStock: MEZAN_READY_CONFIGURATION_STOCK_FIXTURES,
        productId: product.id,
        sku: product.sku,
        color: "silver",
        customerName: "اسم تجريبي",
    });
    expect(result.matched).toBe(true);
    expect(result.quantity_available).toBe(50);
});

test("matches the synthetic gold name but reports zero available", () => {
    const result = matchReadyConfigurationStock({
        readyStock: MEZAN_READY_CONFIGURATION_STOCK_FIXTURES,
        productId: product.id,
        sku: product.sku,
        color: "gold",
        customerName: "اسم تجريبي",
    });
    expect(result.matched).toBe(true);
    expect(result.quantity_available).toBe(0);
});

test("does not match a different customer name", () => {
    const result = matchReadyConfigurationStock({
        readyStock: MEZAN_READY_CONFIGURATION_STOCK_FIXTURES,
        productId: product.id,
        sku: product.sku,
        color: "silver",
        customerName: "اسم مختلف",
    });
    expect(result.matched).toBe(false);
    expect(result.quantity_available).toBe(0);
});
