import {
  canonicalReviewSpecReplacementKey,
  specReplacementButtonLabel,
  specReplacementDisplayText,
  specReplacementPatchPath,
} from "./reviewSpecReplacementEnhancer";


describe("supplier-file spec replacement UI", () => {
  test("canonicalizes the same specification aliases used by the review page", () => {
    expect(canonicalReviewSpecReplacementKey("المقاس:")).toBe("size");
    expect(canonicalReviewSpecReplacementKey(" اللون ")).toBe("color");
    expect(canonicalReviewSpecReplacementKey("ملاحظات")).toBe("ملاحظات");
  });

  test("shows the replacement underneath the original specification", () => {
    expect(specReplacementDisplayText({
      replacement_text: "المقاس 54 انش",
    })).toBe("النص البديل للملف: المقاس 54 انش");
    expect(specReplacementDisplayText({ replacement_text: null })).toBe("");
  });

  test("uses a compact action label", () => {
    expect(specReplacementButtonLabel({ replacement_text: null })).toBe("نص بديل");
    expect(specReplacementButtonLabel({ replacement_text: "المقاس 54 انش" })).toBe("تعديل البديل");
  });

  test("builds the item patch endpoint safely", () => {
    expect(specReplacementPatchPath("272897129", "item/1")).toBe(
      "/order-review-spec-replacements-v1/272897129/items/item%2F1",
    );
  });
});
