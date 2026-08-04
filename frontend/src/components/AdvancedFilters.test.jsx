import {
  defaultFilters,
  presetForRange,
  presetRange,
} from "./AdvancedFilters";

describe("AdvancedFilters shared Arabic date range", () => {
  test("recognizes built-in Riyadh date periods", () => {
    const today = presetRange("today");
    expect(presetForRange(today.from, today.to)).toBe("today");

    const last7 = presetRange("last7");
    expect(presetForRange(last7.from, last7.to)).toBe("last7");
  });

  test("keeps arbitrary calendar choices as custom", () => {
    expect(presetForRange("2026-07-02", "2026-07-16")).toBe("custom");
  });

  test("preserves Dashboard defaults and operational filters", () => {
    expect(defaultFilters("today")).toMatchObject({
      preset: "today",
      payment_methods: [],
      shipping_companies: [],
    });
  });
});
