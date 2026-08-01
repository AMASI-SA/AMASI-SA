import {
  mezanImageDeleteErrorCode,
  mezanImageIdFromUrl,
  mezanImageUnlinkDeletePath,
} from "./reviewMezanImageEnhancer";


describe("Mezan image delete recovery", () => {
  test("extracts a stable image id from the internal image URL", () => {
    expect(mezanImageIdFromUrl("/api/order-reviews-v1/mezan-images/abc123")).toBe("abc123");
    expect(mezanImageIdFromUrl("/api/order-reviews-v1/mezan-images/abc123?x=1")).toBe("abc123");
  });

  test("recognizes the linked-image conflict returned by the normal delete", () => {
    expect(mezanImageDeleteErrorCode({
      response: { data: { detail: { code: "mezan_image_in_use" } } },
    })).toBe("mezan_image_in_use");
    expect(mezanImageDeleteErrorCode(new Error("network"))).toBe("");
  });

  test("builds the explicit unlink-and-delete endpoint safely", () => {
    expect(mezanImageUnlinkDeletePath("272897129", "item/1", "abc 123")).toBe(
      "/order-reviews-v1/272897129/items/item%2F1/mezan-images/abc%20123/unlink-and-delete",
    );
  });
});
