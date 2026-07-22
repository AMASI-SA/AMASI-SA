import {
    MEZAN_COST_RESOURCES_FIXTURES,
    MEZAN_PRODUCT_PREVIEW_FIXTURES,
    MEZAN_PRODUCT_RECIPE_FIXTURES,
} from "../demo/mezanProductPreviewFixtures";
import {
    buildConfigurationKey,
    calculateConfigurationCost,
    getOptionRuleSummary,
    setOptionFixedCostDelta,
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

test("employee option editor replaces the silver delta without duplicating it", () => {
    const updated = setOptionFixedCostDelta(recipe, "color", "silver", 7);
    const summary = getOptionRuleSummary(updated, "color", "silver");
    const result = calculateConfigurationCost({
        recipe: updated,
        resources: MEZAN_COST_RESOURCES_FIXTURES,
        selections: { color: "silver" },
    });
    expect(summary.fixed_cost_delta).toBe(7);
    expect(summary.resource_ids).toEqual(["component-chain-silver"]);
    expect(result.lines.filter((line) => line.type === "fixed_cost_delta")).toHaveLength(1);
    expect(result.known_total).toBe(7);
});

test("employee can add a cost to gold while preserving its stock component", () => {
    const updated = setOptionFixedCostDelta(recipe, "color", "gold", 3);
    const summary = getOptionRuleSummary(updated, "color", "gold");
    expect(summary.fixed_cost_delta).toBe(3);
    expect(summary.resource_ids).toEqual(["component-chain-gold"]);
});
