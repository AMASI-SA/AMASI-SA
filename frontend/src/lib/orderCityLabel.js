// Display aliases only: preserve the canonical delivery address and never
// request city data while rendering the recent-orders list.
const ARABIC_CITY_NAMES = new Map([
    ["al khobar", "الخبر"],
    ["khobar", "الخبر"],
    ["taif", "الطائف"],
    ["at taif", "الطائف"],
    ["arar", "عرعر"],
    ["riyadh", "الرياض"],
    ["ar riyadh", "الرياض"],
    ["dammam", "الدمام"],
    ["ad dammam", "الدمام"],
    ["afif", "عفيف"],
    ["al hair", "الحائر"],
    ["al haer", "الحائر"],
    ["al ha ir", "الحائر"],
    ["al shahaniya", "الشحانية"],
    ["al shahaniyah", "الشحانية"],
    ["ash shahaniyah", "الشحانية"],
    ["khamis mushait", "خميس مشيط"],
    ["khamis mushayt", "خميس مشيط"],
]);

function cityText(value) {
    return typeof value === "string" ? value.trim() : "";
}

export function arabicCityLabel(city) {
    const original = cityText(city);
    if (!original) return "غير محدد";
    const key = original.toLowerCase().replace(/[-_'’]+/g, " ").replace(/\s+/g, " ");
    return ARABIC_CITY_NAMES.get(key) || original;
}

export function orderCityLabel(order) {
    // The delivery address retains precedence over the customer's address;
    // an Arabic label on a different address must not change the destination.
    const city = cityText(order?.shipping?.address?.city)
        || cityText(order?.customer?.shipping_address?.city);
    return arabicCityLabel(city);
}
