import fs from "fs";
import path from "path";

import {
  displayReviewedQuantity,
  filterReviewedProducts,
  reviewedProductMatchesCategories,
  selectedReviewedCategoryNames,
  toggleReviewedCategory,
} from "./reviewedProductFilters";
import {
  findReviewedProductForCard,
  reviewedProductSortButtonLabel,
  reviewedProductSortCandidateSummary,
  updateReviewedProductSortPreference,
} from "./reviewedProductSortUi";

const products = [
  {
    group_key: "product:1",
    name: "سلسال بالاسم",
    sku: "NAME-1",
    category_ids: ["accessories", "necklaces"],
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

test("search works with product name sku and product id", () => {
  expect(filterReviewedProducts(products, [], "كوتش")).toEqual([products[1]]);
  expect(filterReviewedProducts(products, [], "NAME-1")).toEqual([products[0]]);
});

test("quantity and selected labels stay phone friendly", () => {
  expect(displayReviewedQuantity(50)).toBe("50");
  expect(displayReviewedQuantity(2.5)).toBe("2.5");
  expect(selectedReviewedCategoryNames(
    [{ id: "bags", name: "الشنط" }, { id: "necklaces", name: "السلاسل" }],
    ["necklaces"],
  )).toEqual(["السلاسل"]);
});

test("sort candidate summary shows highest piece demand first", () => {
  expect(reviewedProductSortCandidateSummary({
    values: [
      { value: "5 سنوات", quantity: 30 },
      { value: "6 سنوات", quantity: 12 },
    ],
  })).toBe("5 سنوات (30 قطعة)، 6 سنوات (12 قطعة)");
});

test("sort button reflects the saved one-field preference", () => {
  expect(reviewedProductSortButtonLabel({ preparation_sort_label: "العمر" }))
    .toBe("ترتيب الملف: العمر");
  expect(reviewedProductSortButtonLabel({}))
    .toBe("تحديد ترتيب الملف");
});

test("visible reviewed card maps to its product by name and sku", () => {
  expect(findReviewedProductForCard(products, {
    name: "شنطة كوتش",
    sku: "BAG-2",
  })?.group_key).toBe("product:2");
});

test("saved preference updates only sort metadata", () => {
  const updated = updateReviewedProductSortPreference(products[0], {
    spec_key: "اللون",
    spec_label: "اللون",
    candidates: [{ key: "اللون", label: "اللون", values: [] }],
  });
  expect(updated.group_key).toBe("product:1");
  expect(updated.preparation_sort_spec).toBe("اللون");
  expect(updated.preparation_sort_candidates).toHaveLength(1);
});

test("sort manager renders a compact thumbnail beside each product name", () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, "reviewedProductSortEnhancer.js"),
    "utf8",
  );

  expect(source).toContain('data-testid", "reviewed-sort-product-thumbnail"');
  expect(source).toContain("product?.image_url");
  expect(source).toContain("object-fit:cover");
  expect(source).toContain("identity.append(thumbnail, nameBox)");
});
