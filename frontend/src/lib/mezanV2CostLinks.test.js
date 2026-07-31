import { buildMissingMezanCostHref } from "./mezanV2CostLinks";


test("one missing product opens its Mezan V2 editor directly", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [{ mezan_product_id: "m-1", salla_product_id: "p-1" }],
    }, { from: "2026-08-01", to: "2026-08-01" });

    expect(href).toContain("/products-v2?");
    expect(href).toContain("workspace=intake");
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).toContain("sold_only=1");
    expect(href).toContain("product=m-1");
    expect(href).toContain("focus=cost");
    expect(href).toContain("from=2026-08-01");
});


test("multiple missing products open the filtered sold-products list", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [
            { mezan_product_id: "m-1" },
            { mezan_product_id: "m-2" },
        ],
    });

    expect(href).toContain("workspace=intake");
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).not.toContain("product=");
});


test("an unmapped order line opens the filtered list instead of a missing editor", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [{
            salla_product_id: "not-in-catalog",
            catalog_product_found: false,
        }],
    });

    expect(href).not.toContain("product=");
    expect(href).not.toContain("focus=cost");
});
