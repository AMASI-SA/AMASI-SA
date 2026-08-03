import {
  REVIEW_SPEC_EDIT_VISIBILITY_CSS,
  installReviewSpecEditVisibilityStyle,
} from "./reviewSpecEditVisibilitySafety";

beforeEach(() => {
  document.head.innerHTML = "";
});

test("collapsed product hides edit-file and hide-file controls", () => {
  expect(REVIEW_SPEC_EDIT_VISIBILITY_CSS).toContain(
    '[data-review-product-editing="false"]',
  );
  expect(REVIEW_SPEC_EDIT_VISIBILITY_CSS).toContain(
    "button[data-spec-replacement-action]",
  );
  expect(REVIEW_SPEC_EDIT_VISIBILITY_CSS).toContain(
    "button[data-export-spec-action]",
  );
  expect(REVIEW_SPEC_EDIT_VISIBILITY_CSS).toContain(
    "display: none !important",
  );
});

test("editing product restores both controls", () => {
  expect(REVIEW_SPEC_EDIT_VISIBILITY_CSS).toContain(
    '[data-review-product-editing="true"]',
  );
  expect(REVIEW_SPEC_EDIT_VISIBILITY_CSS).toContain(
    "display: inline-flex !important",
  );
});

test("style is installed once after the earlier layout override", () => {
  const first = installReviewSpecEditVisibilityStyle(document);
  const second = installReviewSpecEditVisibilityStyle(document);
  expect(first).toBe(second);
  expect(document.head.querySelectorAll("style")).toHaveLength(1);
});
