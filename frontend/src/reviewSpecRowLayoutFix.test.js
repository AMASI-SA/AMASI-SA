import {
  REVIEW_SPEC_ROW_LAYOUT_CSS,
  arrangeReviewSpecRow,
} from "./reviewSpecRowLayoutFix";

beforeEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
});

test("spec row keeps full-width text and moves both file controls below it", () => {
  const row = document.createElement("div");
  row.innerHTML = `
    <span>العبارة داخل الفنجال في الأسفل:</span>
    <span>الحياة أجمل حينما ترى نصف الكوب مليئًا</span>
    <button data-export-spec-action="1">إخفاء من الملف</button>
    <div data-spec-replacement-tools="1">
      <button data-spec-replacement-action="1">تعديل للملف</button>
    </div>
  `;
  document.body.appendChild(row);

  const result = arrangeReviewSpecRow(row);

  expect(result).not.toBeNull();
  expect(row.dataset.reviewSpecLayout).toBe("1");
  expect(row.children[0].dataset.reviewSpecLabel).toBe("1");
  expect(row.children[1].dataset.reviewSpecValue).toBe("1");

  const actions = row.querySelector('[data-review-spec-actions="1"]');
  expect(actions).not.toBeNull();
  expect(actions.querySelector('[data-export-spec-action="1"]')).not.toBeNull();
  expect(actions.querySelector('[data-spec-replacement-action="1"]')).not.toBeNull();

  expect(REVIEW_SPEC_ROW_LAYOUT_CSS).toContain(
    'button[data-spec-replacement-action]',
  );
  expect(REVIEW_SPEC_ROW_LAYOUT_CSS).toContain(
    'grid-template-columns: minmax(110px, 36%) minmax(0, 1fr)',
  );
  expect(REVIEW_SPEC_ROW_LAYOUT_CSS).toContain("word-break: normal");
});
