import {
    preparationFileMetadataPayload,
    preparationFileRecordLabel,
    readSelectedProductCount,
    readSelectionMetric,
    riyadhDateParts,
} from "./preparationFileRegistryUi";


test("Riyadh date is shown in ISO and merchant display formats", () => {
    const result = riyadhDateParts(new Date("2026-08-02T12:00:00Z"));
    expect(result).toEqual({ iso: "2026-08-02", display: "2026/8/2" });
});

test("reads the selected products and pieces from the fixed selection bar", () => {
    document.body.innerHTML = `
      <div data-testid="reviewed-preparation-selection-bar">
        <div><div>المنتجات المحددة</div><div>2</div></div>
        <div><div>قطع هذا الملف</div><div>30</div></div>
      </div>`;
    const bar = document.querySelector('[data-testid="reviewed-preparation-selection-bar"]');
    expect(readSelectionMetric(bar, "المنتجات المحددة")).toBe(2);
    expect(readSelectionMetric(bar, "قطع هذا الملف")).toBe(30);
});

test("reads the selected card count used by the reviewed piece-card layout", () => {
    document.body.innerHTML = `
      <div data-testid="reviewed-preparation-selection-bar">
        <div><div>البطاقات المحددة</div><div>3</div></div>
      </div>`;
    const bar = document.querySelector('[data-testid="reviewed-preparation-selection-bar"]');
    expect(readSelectedProductCount(bar)).toBe(3);
});

test("metadata payload keeps the responsible employee and immutable counts", () => {
    expect(preparationFileMetadataPayload({
        fileTitle: " دفعة السلاسل ",
        responsibleEmployeeId: " employee-1 ",
        expectedQuantity: 30,
        selectedProductCount: 2,
    })).toEqual({
        fileTitle: "دفعة السلاسل",
        responsibleEmployeeId: "employee-1",
        expectedQuantity: 30,
        selectedProductCount: 2,
    });
});

test("history label starts with the permanent file number", () => {
    expect(preparationFileRecordLabel({
        file_number: "PF-20260802-0001",
        file_title: "دفعة السلاسل",
    })).toBe("PF-20260802-0001 — دفعة السلاسل");
});
