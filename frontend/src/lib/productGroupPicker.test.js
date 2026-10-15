import {
    filterProductGroups,
    groupNamesForResource,
    resourceMatchesCategory,
    toggleSelectedGroup,
} from "./productGroupPicker";

const groups = [
    { id: "service-plating", category_id: "plating", group_kind: "service", name: "قص - طلاء" },
    { id: "component-plating", category_id: "plating", group_kind: "component", name: "كيس - علبة" },
    { id: "service-clothes", category_id: "clothes", group_kind: "service", name: "تطريز - طباعة" },
];

test("group modal shows only groups for selected category and kind", () => {
    expect(filterProductGroups(groups, { categoryId: "plating", kind: "service" }))
        .toEqual([groups[0]]);
    expect(filterProductGroups(groups, { categoryId: "plating", kind: "component" }))
        .toEqual([groups[1]]);
    expect(filterProductGroups(groups, { categoryId: "", kind: "service" }))
        .toEqual([]);
});

test("resources are filtered by selected category", () => {
    const bag = { category_ids: ["plating", "clothes"] };
    expect(resourceMatchesCategory(bag, "plating")).toBe(true);
    expect(resourceMatchesCategory(bag, "clothes")).toBe(true);
    expect(resourceMatchesCategory(bag, "gifts")).toBe(false);
});

test("group selection toggles without duplicates", () => {
    let selected = toggleSelectedGroup([], "g1");
    selected = toggleSelectedGroup(selected, "g2");
    expect(selected).toEqual(["g1", "g2"]);
    selected = toggleSelectedGroup(selected, "g1");
    expect(selected).toEqual(["g2"]);
});

test("resource provenance resolves linked group names", () => {
    expect(groupNamesForResource(groups, ["service-plating", "service-clothes"]))
        .toEqual(["قص - طلاء", "تطريز - طباعة"]);
});
