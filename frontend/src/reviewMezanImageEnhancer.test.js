import {
  mezanImageDeleteErrorCode,
  mezanImageIdFromUrl,
  mezanImageUnlinkDeletePath,
} from "./reviewMezanImageEnhancer";
import {
  isMezanGlobalDeleteButton,
  itemForDeleteCard,
  mezanGlobalDeleteConfirmationText,
  mezanGlobalDeletePath,
  mezanGlobalImageId,
} from "./reviewMezanImageGlobalDelete";


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

  test("global delete extracts the image and calls the same safe endpoint", () => {
    expect(mezanGlobalImageId(
      "https://mezansalla.com/api/order-reviews-v1/mezan-images/shared-1?cache=1",
    )).toBe("shared-1");
    expect(mezanGlobalDeletePath("1001", "item/1", "shared 1")).toBe(
      "/order-reviews-v1/1001/items/item%2F1/mezan-images/shared%201/unlink-and-delete",
    );
  });

  test("global delete confirmation explains every linked order returns to Salla", () => {
    const message = mezanGlobalDeleteConfirmationText();
    expect(message).toContain("جميع الطلبات");
    expect(message).toContain("صورة سلة الافتراضية");
    expect(message).toContain("لن تتأثر صور سلة الأصلية");
  });

  test("intercepts only a delete button inside Mezan image tools", () => {
    const card = document.createElement("article");
    card.innerHTML = `
      <div data-mezan-image-tools>
        <div><img src="/api/order-reviews-v1/mezan-images/abc"><button>حذف</button></div>
      </div>
      <button data-outside>حذف</button>
    `;
    const inside = card.querySelector("[data-mezan-image-tools] button");
    const outside = card.querySelector("[data-outside]");

    expect(isMezanGlobalDeleteButton(inside)).toBe(inside);
    expect(isMezanGlobalDeleteButton(outside)).toBeNull();
  });

  test("matches the delete card to the correct order item by index then SKU", () => {
    const cardA = document.createElement("article");
    const cardB = document.createElement("article");
    cardA.innerHTML = "<span>SKU: AMS1</span>";
    cardB.innerHTML = "<span>SKU: AMS2</span>";
    const detail = {
      items: [
        { order_item_id: "item-a", sku: "AMS1" },
        { order_item_id: "item-b", sku: "AMS2" },
      ],
    };

    expect(itemForDeleteCard(detail, cardB, [cardA, cardB])).toEqual(
      detail.items[1],
    );
    expect(itemForDeleteCard(detail, cardA, [])).toEqual(detail.items[0]);
  });
});
