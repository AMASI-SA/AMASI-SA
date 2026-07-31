import { normalize_saudi_mobile } from "./normalizeSaudiMobile";


describe("Saudi mobile normalization", () => {
  test("maps supported Saudi formats to one canonical customer number", () => {
    const expected = "966570076958";

    expect(normalize_saudi_mobile("570076958")).toBe(expected);
    expect(normalize_saudi_mobile("0570076958")).toBe(expected);
    expect(normalize_saudi_mobile("+966570076958")).toBe(expected);
  });

  test("accepts the international access-code form", () => {
    expect(normalize_saudi_mobile("00966570076958")).toBe("966570076958");
  });
});
