import {
    clampReviewedPreparationQuantity,
    reconcileReviewedPreparationSelection,
    reviewedPreparationSelectionSummary,
    setReviewedPreparationQuantity,
    toggleReviewedPreparationProduct,
} from "./reviewedPreparationSelection";

const product = (key, quantity) => ({
    group_key: key,
    quantity,
    remaining_quantity: quantity,
});

const piece = (key) => ({
    ...product(key, 1),
    piece_level: true,
});

test("selecting a product defaults to its full remaining quantity", () => {
    expect(toggleReviewedPreparationProduct({}, product("p-1", 50)))
        .toEqual({ "p-1": 50 });
});

test("selecting thirty of fifty keeps the requested partial quantity", () => {
    const selected = setReviewedPreparationQuantity(
        { "p-1": 50 },
        product("p-1", 50),
        30,
    );
    expect(selected).toEqual({ "p-1": 30 });
    expect(reviewedPreparationSelectionSummary(selected)).toEqual({
        selections: [{ group_key: "p-1", quantity: 30 }],
        productCount: 1,
        totalQuantity: 30,
    });
});

test("quantity is clamped between one and remaining", () => {
    expect(clampReviewedPreparationQuantity(product("p-1", 20), 0)).toBe(1);
    expect(clampReviewedPreparationQuantity(product("p-1", 20), 100)).toBe(20);
    expect(clampReviewedPreparationQuantity(product("p-1", 20), "7.9")).toBe(7);
});

test("multiple products produce one sorted file payload", () => {
    const summary = reviewedPreparationSelectionSummary({
        "product:p-2": 10,
        "product:p-1": 30,
    });
    expect(summary.productCount).toBe(2);
    expect(summary.totalQuantity).toBe(40);
    expect(summary.selections).toEqual([
        { group_key: "product:p-1", quantity: 30 },
        { group_key: "product:p-2", quantity: 10 },
    ]);
});

test("selection is reduced after another employee allocates units", () => {
    expect(reconcileReviewedPreparationSelection(
        { "p-1": 30, "p-gone": 5 },
        [product("p-1", 20)],
    )).toEqual({ "p-1": 20 });
});

test("pressing a selected product removes it from the file", () => {
    expect(toggleReviewedPreparationProduct(
        { "p-1": 30 },
        product("p-1", 50),
    )).toEqual({});
});

test("a physical-piece card always contributes exactly one unit", () => {
    expect(toggleReviewedPreparationProduct({}, piece("ready-unit:1")))
        .toEqual({ "ready-unit:1": 1 });
    expect(setReviewedPreparationQuantity(
        { "ready-unit:1": 1 },
        piece("ready-unit:1"),
        50,
    )).toEqual({ "ready-unit:1": 1 });
    expect(reconcileReviewedPreparationSelection(
        { "ready-unit:1": 9 },
        [piece("ready-unit:1")],
    )).toEqual({ "ready-unit:1": 1 });
});
