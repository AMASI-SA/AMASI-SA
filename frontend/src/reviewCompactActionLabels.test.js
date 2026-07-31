import {
  compactReviewActionElement,
  compactReviewActionLabel,
} from "./reviewCompactActionLabels";


describe("compact review action labels", () => {
  test("maps the five review buttons to compact labels", () => {
    expect(compactReviewActionLabel("إضافة منتج تشغيلي")).toBe("منتج");
    expect(compactReviewActionLabel("تعليمات التجهيز")).toBe("تعليمات");
    expect(compactReviewActionLabel("ملاحظة داخلية")).toBe("ملاحظة");
    expect(compactReviewActionLabel("توجيه مباشر للتجهيز الداخلي")).toBe("توجيه للتجهيز");
    expect(compactReviewActionLabel("إضافة صورة ميزان")).toBe("صورة ميزان");
  });

  test("keeps icons while replacing only the visible long label", () => {
    const button = document.createElement("button");
    button.innerHTML = '<span data-icon="1">+</span> إضافة منتج تشغيلي';

    expect(compactReviewActionElement(button)).toBe(true);
    expect(button.querySelector("[data-icon='1']")).not.toBeNull();
    expect(button.textContent).toContain("منتج");
    expect(button.textContent).not.toContain("إضافة منتج تشغيلي");
    expect(button.getAttribute("aria-label")).toBe("إضافة منتج تشغيلي");
    expect(button.title).toBe("إضافة منتج تشغيلي");
  });

  test("does not change unrelated actions", () => {
    const button = document.createElement("button");
    button.textContent = "تمت المراجعة";

    expect(compactReviewActionElement(button)).toBe(false);
    expect(button.textContent).toBe("تمت المراجعة");
  });
});
