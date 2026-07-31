import {
  canonicalReviewSpecKey,
  isInternalPreparationRoute,
  nextManualHiddenKeys,
} from "./reviewExportControlsEnhancer";


describe("review export controls", () => {
  test("canonicalizes review field names", () => {
    expect(canonicalReviewSpecKey("اللون:")).toBe("color");
    expect(canonicalReviewSpecKey(" اسحب وافلت الصورة هنا ")).toBe(
      "اسحب وافلت الصورة هنا",
    );
  });

  test("toggles one manually hidden field without deleting other fields", () => {
    expect(nextManualHiddenKeys([], "اسحب وافلت الصورة هنا")).toEqual([
      "اسحب وافلت الصورة هنا",
    ]);
    expect(
      nextManualHiddenKeys(
        ["الاسم", "اسحب وافلت الصورة هنا"],
        "اسحب وافلت الصورة هنا",
      ),
    ).toEqual(["الاسم"]);
  });

  test("recognizes items routed directly to internal preparation", () => {
    expect(isInternalPreparationRoute({
      preparation_route: "internal_preparation",
      supplier_export: false,
    })).toBe(true);
    expect(isInternalPreparationRoute({
      preparation_route: "supplier_file",
      supplier_export: true,
    })).toBe(false);
  });
});
