import {
  displayReviewedQuantity,
  filterReviewedProducts,
  reviewedProductMatchesCategories,
  selectedReviewedCategoryNames,
  toggleReviewedCategory,
} from "./reviewedProductFilters";

const products = [
  {
    group_key: "product:1",
    name: "سلسال بالاسم",
    sku: "NAME-1",
    category_ids: ["accessories", "necklaces"],
    source_order_numbers: ["279800001"],
  },
  {
    group_key: "product:2",
    name: "شنطة كوتش",
    sku: "BAG-2",
    category_ids: ["bags"],
  },
];

test("no category selection means all reviewed products", () => {
  expect(filterReviewedProducts(products, [], "")).toHaveLength(2);
});

test("multiple selected categories use OR semantics", () => {
  expect(filterReviewedProducts(products, ["necklaces", "bags"], ""))
    .toHaveLength(2);
  expect(filterReviewedProducts(products, ["necklaces"], ""))
    .toEqual([products[0]]);
});

test("parent category matches product through expanded category ids", () => {
  expect(reviewedProductMatchesCategories(products[0], ["accessories"]))
    .toBe(true);
});

test("category selection toggles without duplicates", () => {
  expect(toggleReviewedCategory([], "bags")).toEqual(["bags"]);
  expect(toggleReviewedCategory(["bags"], "bags")).toEqual([]);
  expect(toggleReviewedCategory(["bags"], "necklaces").sort())
    .toEqual(["bags", "necklaces"]);
});

test("search works with product name sku product id and order number", () => {
  expect(filterReviewedProducts(products, [], "كوتش")).toEqual([products[1]]);
  expect(filterReviewedProducts(products, [], "NAME-1")).toEqual([products[0]]);
  expect(filterReviewedProducts(products, [], "279800001")).toEqual([products[0]]);
});

test("quantity and selected labels stay phone friendly", () => {
  expect(displayReviewedQuantity(50)).toBe("50");
  expect(displayReviewedQuantity(2.5)).toBe("2.5");
  expect(selectedReviewedCategoryNames(
    [{ id: "bags", name: "الشنط" }, { id: "necklaces", name: "السلاسل" }],
    ["necklaces"],
  )).toEqual(["السلاسل"]);
});
