import {
  canonicalReviewSpecReplacementKey,
  hasSpecFieldReplacement,
  specReplacementButtonLabel,
  specReplacementDisplayLines,
  specReplacementDisplayText,
  specReplacementPatchPath,
} from "./reviewSpecReplacementEnhancer";


describe("supplier-file spec field overrides UI", () => {
  test("canonicalizes the same specification aliases used by the review page", () => {
    expect(canonicalReviewSpecReplacementKey("المقاس:")).toBe("size");
    expect(canonicalReviewSpecReplacementKey(" اللون ")).toBe("color");
    expect(canonicalReviewSpecReplacementKey("ملاحظات")).toBe("ملاحظات");
  });

  test("shows name and value overrides as separate lines", () => {
    expect(specReplacementDisplayLines({
      replacement_name: "المقاس المطلوب",
      replacement_value: "54 بوصة",
    })).toEqual([
      "اسم المواصفة في الملف: المقاس المطلوب",
      "قيمة المواصفة في الملف: 54 بوصة",
    ]);
    expect(specReplacementDisplayText({
      replacement_name: null,
      replacement_value: "54 بوصة",
    })).toBe("قيمة المواصفة في الملف: 54 بوصة");
  });

  test("recognizes independent name or value changes", () => {
    expect(hasSpecFieldReplacement({ replacement_name: "المقاس المطلوب", replacement_value: null })).toBe(true);
    expect(hasSpecFieldReplacement({ replacement_name: null, replacement_value: "54 بوصة" })).toBe(true);
    expect(hasSpecFieldReplacement({ replacement_name: null, replacement_value: null })).toBe(false);
  });

  test("uses a compact field-edit action label", () => {
    expect(specReplacementButtonLabel({ replacement_name: null, replacement_value: null })).toBe("تعديل للملف");
    expect(specReplacementButtonLabel({ replacement_name: null, replacement_value: "54 بوصة" })).toBe("تعديل الحقول");
  });

  test("builds the item patch endpoint safely", () => {
    expect(specReplacementPatchPath("272897129", "item/1")).toBe(
      "/order-review-spec-replacements-v1/272897129/items/item%2F1",
    );
  });
});
