const STYLE_ID = "mezan-review-spec-edit-visibility-safety";

export const REVIEW_SPEC_EDIT_VISIBILITY_CSS = `
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  [data-review-spec-actions="1"],
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  button[data-spec-replacement-action],
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  button[data-export-spec-action] {
  display: none !important;
}
[data-testid="order-review-product-card"][data-review-product-editing="true"]
  [data-review-spec-actions="1"] {
  display: flex !important;
}
[data-testid="order-review-product-card"][data-review-product-editing="true"]
  button[data-spec-replacement-action],
[data-testid="order-review-product-card"][data-review-product-editing="true"]
  button[data-export-spec-action] {
  display: inline-flex !important;
}
`;

export function installReviewSpecEditVisibilityStyle(documentLike = document) {
  if (!documentLike?.head) return null;
  const existing = documentLike.getElementById(STYLE_ID);
  if (existing) return existing;
  const style = documentLike.createElement("style");
  style.id = STYLE_ID;
  style.textContent = REVIEW_SPEC_EDIT_VISIBILITY_CSS;
  documentLike.head.appendChild(style);
  return style;
}

if (typeof document !== "undefined") {
  installReviewSpecEditVisibilityStyle(document);
}
