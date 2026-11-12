import { arabicCityLabel, orderCityLabel } from "./orderCityLabel";

test.each([
    ["Al Khobar", "الخبر"],
    ["Taif", "الطائف"],
    ["Arar", "عرعر"],
    ["Riyadh", "الرياض"],
    ["Dammam", "الدمام"],
    ["Afif", "عفيف"],
    ["AL HAIR", "الحائر"],
    ["Al Shahaniya", "الشحانية"],
    ["Khamis Mushait", "خميس مشيط"],
])("recent-order destination %s has its Arabic display name", (city, expected) => {
    expect(orderCityLabel({ shipping: { address: { city } } })).toBe(expected);
});

test("case, surrounding whitespace and known transliteration separators do not affect labels", () => {
    expect(arabicCityLabel("  aL   KHOBAR  ")).toBe("الخبر");
    expect(arabicCityLabel("Ar-Riyadh")).toBe("الرياض");
    expect(arabicCityLabel("Al-Ha'ir")).toBe("الحائر");
});

test("existing Arabic and unknown city names remain source facts", () => {
    expect(arabicCityLabel("  الحائر  ")).toBe("الحائر");
    expect(arabicCityLabel("Unknown destination")).toBe("Unknown destination");
    expect(arabicCityLabel("Riyadh Road")).toBe("Riyadh Road");
});

test("delivery city wins over a different Arabic customer city without mutating the order", () => {
    const order = {
        shipping: { address: { city: "Dammam" } },
        customer: { shipping_address: { city: "الرياض" } },
    };
    const original = JSON.stringify(order);
    expect(orderCityLabel(order)).toBe("الدمام");
    expect(JSON.stringify(order)).toBe(original);
});

test("a missing delivery city falls back to the canonical customer shipping address", () => {
    expect(orderCityLabel({
        shipping: { address: { city: "  " } },
        customer: { shipping_address: { city: "Taif" } },
    })).toBe("الطائف");
});

test.each([undefined, null, {}, { shipping: { address: { city: " " } } }])(
    "missing cities retain the unknown label",
    (order) => expect(orderCityLabel(order)).toBe("غير محدد"),
);
