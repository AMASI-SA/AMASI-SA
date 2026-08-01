import {
    buildMezanProductCostHref,
    buildMissingMezanCostHref,
    resolveInitialProductsView,
    resolveInitialSelectedProduct,
} from "./mezanV2CostLinks";


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


test("multiple missing products open the filtered sold-products list with dashboard filters", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [
            { mezan_product_id: "m-1" },
            { mezan_product_id: "m-2" },
        ],
    }, {
        from: "2026-08-01",
        to: "2026-08-02",
        payment_methods: ["مدى", "Apple Pay"],
        shipping_companies: ["سمسا"],
    });

    expect(href).toContain("workspace=intake");
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).toContain("sold_only=1");
    expect(href).toContain("view=list");
    expect(decodeURIComponent(href)).toContain("payment_methods=مدى,Apple+Pay");
    expect(decodeURIComponent(href)).toContain("shipping_companies=سمسا");
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


test("a fallback-cost product opens its Mezan editor with the sold-missing filter", () => {
    const href = buildMezanProductCostHref({
        mezan_product_id: "m-fallback",
        salla_product_id: "p-fallback",
        catalog_product_found: true,
        cost_status: "salla_fallback",
    }, { from: "2026-08-01", to: "2026-08-02" });

    expect(href).toContain("workspace=intake");
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).toContain("sold_only=1");
    expect(href).toContain("product=m-fallback");
    expect(href).toContain("focus=cost");
    expect(href).toContain("from=2026-08-01");
    expect(href).toContain("to=2026-08-02");
});


test("a costed product can still open its product cost editor without a missing filter", () => {
    const href = buildMezanProductCostHref({
        salla_product_id: "p-complete",
        catalog_product_found: true,
        cost_status: "complete",
    });

    expect(href).toContain("product=p-complete");
    expect(href).toContain("focus=cost");
    expect(href).not.toContain("missing_mezan_cost");
    expect(href).not.toContain("sold_only");
});


test("sold-missing list ignores the previously stored product", () => {
    const search = "?workspace=intake&missing_mezan_cost=1&sold_only=1&view=list";

    expect(resolveInitialSelectedProduct(search, "old-product")).toBe("");
    expect(resolveInitialProductsView(search, "old-product")).toBe("list");
});


test("a direct cost link still opens the requested product details", () => {
    const search = "?workspace=intake&product=m-7&focus=cost";

    expect(resolveInitialSelectedProduct(search, "old-product")).toBe("m-7");
    expect(resolveInitialProductsView(search, "old-product")).toBe("detail");
});
