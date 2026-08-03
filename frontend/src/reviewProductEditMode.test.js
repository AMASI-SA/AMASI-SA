import { toast } from "sonner";

import {
  REVIEW_PRODUCT_EDIT_MODE_CSS,
  decorateReviewProductCard,
  isReviewProductCardEditing,
  markReviewProductEditOnlyRegions,
  persistOpenReviewProductNotes,
  reviewProductQuantityValue,
} from "./reviewProductEditMode";
import {
  REVIEW_DIALOG_EDIT_CONTROL_CSS,
  enhanceReviewEditToggle,
  enhanceReviewImageDialog,
} from "./reviewImageDialogAndEditControlEnhancer";


jest.mock("sonner", () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
  },
}));

function productCard() {
  const card = document.createElement("article");
  card.dataset.testid = "order-review-product-card";
  card.innerHTML = `
    <div class="grid">
      <div><img src="main.jpg" alt="product"></div>
      <div>
        <h3>شنطة كوتش تابي</h3>
        <div>
          <span>SKU: <b>AMS11936</b></span>
          <span>الكمية: <b>3</b></span>
        </div>
        <div class="mt-4">
          <div>
            <button type="button" aria-label="اختيار صورة التجهيز رقم 1">صورة</button>
          </div>
        </div>
      </div>
    </div>
    <div class="border-t product-specs">
      <button type="button" data-export-spec-action="1">إخفاء من الملف</button>
      <button type="button" data-spec-replacement-action="1">تعديل الحقول</button>
    </div>
    <div class="border-t product-actions">
      <div>
        <button
          type="button"
          data-review-full-label="إضافة منتج تشغيلي"
          aria-label="إضافة منتج تشغيلي"
        >منتج</button>
        <button type="button">تعليمات</button>
        <button type="button">ملاحظة</button>
      </div>
      <button type="button">حفظ الملاحظات</button>
    </div>
    <div data-mezan-image-tools="1">
      <button type="button">إضافة صورة ميزان</button>
    </div>
  `;
  document.body.appendChild(card);
  return card;
}

beforeEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  jest.clearAllMocks();
});

test("collapsed product card keeps only the pencil and styles quantity as a green number badge", () => {
  const card = productCard();
  decorateReviewProductCard(card, { key: "order-1|AMS11936|0" });

  expect(isReviewProductCardEditing(card)).toBe(false);
  expect(card.querySelector('[data-review-edit-toggle="1"]')?.getAttribute("aria-label"))
    .toBe("تعديل المنتج");
  expect(reviewProductQuantityValue(card)).toBe("3");

  const quantity = card.querySelector('[data-review-product-quantity="1"]');
  expect(quantity).not.toBeNull();
  expect(quantity.getAttribute("aria-label")).toBe("الكمية 3");
  expect(quantity.style.background).toBe("rgb(5, 150, 105)");
  expect(quantity.style.fontSize).toBe("0px");
  expect(quantity.querySelector("b").style.fontSize).toBe("17px");

  const regions = markReviewProductEditOnlyRegions(card);
  expect(regions.gallery?.dataset.reviewEditOnly).toBe("image-gallery");
  expect(regions.actionSection?.dataset.reviewEditOnly).toBe("product-actions");
  expect(regions.notesSave?.dataset.reviewProductSubsave).toBe("notes");

  expect(REVIEW_PRODUCT_EDIT_MODE_CSS).toContain(
    'button:not([data-review-edit-toggle])',
  );
  expect(REVIEW_PRODUCT_EDIT_MODE_CSS).toContain("[data-mezan-image-tools]");
  expect(REVIEW_PRODUCT_EDIT_MODE_CSS).toContain("[data-review-edit-only]");
});

test("pencil opens all product editing controls and save closes them again", async () => {
  const card = productCard();
  decorateReviewProductCard(card, { key: "order-1|AMS11936|0" });

  let toggle = card.querySelector('[data-review-edit-toggle="1"]');
  toggle.click();

  expect(isReviewProductCardEditing(card)).toBe(true);
  toggle = card.querySelector('[data-review-edit-toggle="1"]');
  expect(toggle.getAttribute("aria-label")).toBe("حفظ وإغلاق تعديلات المنتج");
  expect(toggle.textContent).toContain("حفظ");

  toggle.click();
  // The central save gives React one tick to submit any open note editor first.
  await new Promise((resolve) => setTimeout(resolve, 160));

  expect(isReviewProductCardEditing(card)).toBe(false);
  expect(card.querySelector('[data-review-edit-toggle="1"]')?.getAttribute("aria-label"))
    .toBe("تعديل المنتج");
  expect(toast.success).toHaveBeenCalledWith("تم حفظ تعديلات المنتج وإغلاقها.");
});

test("final card save triggers the hidden notes save when note editors are open", async () => {
  const card = productCard();
  const notesSave = card.querySelector(".product-actions > button");
  const handler = jest.fn();
  notesSave.addEventListener("click", handler);
  markReviewProductEditOnlyRegions(card);

  await persistOpenReviewProductNotes(card);

  expect(handler).toHaveBeenCalledTimes(1);
});

test("collapsed card exposes a visible edit-options label instead of a hidden pencil-only control", () => {
  const card = productCard();
  decorateReviewProductCard(card, { key: "order-1|AMS11936|0" });
  const toggle = card.querySelector('[data-review-edit-toggle="1"]');

  enhanceReviewEditToggle(toggle);

  expect(toggle.getAttribute("aria-label")).toBe("تعديل خيارات المنتج");
  expect(toggle.textContent).toContain("تعديل خيارات المنتج");
  expect(toggle.querySelector("[data-review-edit-visible-label]")).not.toBeNull();
  expect(REVIEW_DIALOG_EDIT_CONTROL_CSS).toContain("position: relative !important");
});

test("image-choice dialog keeps close and save actions in a dedicated visible footer", () => {
  const card = productCard();
  decorateReviewProductCard(card, { key: "order-1|AMS11936|0" });
  const overlay = document.createElement("div");
  overlay.className = "fixed";
  overlay.innerHTML = `
    <div class="panel">
      <div class="header">
        <div><h3>حفظ صورة التجهيز</h3><p>اختر طريقة استخدام الصورة داخل ميزان.</p></div>
        <button type="button" data-close>×</button>
      </div>
      <div class="body">
        <label><input type="checkbox" checked> المقاس: 12 سنة</label>
        <div class="actions">
          <button type="button">حفظ لهذا الطلب فقط</button>
          <button type="button">حفظ مع الخيارات المحددة</button>
          <button type="button">حفظ كصورة رئيسية في ميزان</button>
        </div>
      </div>
    </div>
  `;
  card.appendChild(overlay);
  const heading = overlay.querySelector("h3");
  const originalOptions = [...overlay.querySelectorAll("button")].find((button) =>
    button.textContent.includes("حفظ مع الخيارات المحددة"),
  );
  const handler = jest.fn();
  originalOptions.addEventListener("click", handler);

  const enhanced = enhanceReviewImageDialog(heading);

  expect(enhanced.panel.dataset.reviewImageDialogPanel).toBe("1");
  expect(enhanced.header.dataset.reviewImageDialogHeader).toBe("1");
  expect(enhanced.body.dataset.reviewImageDialogBody).toBe("1");
  expect(enhanced.header.querySelector("button").dataset.reviewImageDialogClose).toBe("1");
  expect(enhanced.panel.querySelector("[data-review-image-dialog-original-actions]")).not.toBeNull();
  expect(enhanced.footer.querySelectorAll("button")).toHaveLength(4);

  enhanced.footer.querySelector('[data-review-image-proxy="options"]').click();
  expect(handler).toHaveBeenCalledTimes(1);
  expect(REVIEW_DIALOG_EDIT_CONTROL_CSS).toContain("display: inline-flex !important");
});
