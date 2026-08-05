import {
    changeComponentKind,
    componentFormCanSave,
    newComponentForm,
    toggleComponentCategory,
} from "./componentCreationRules";

test("new services block shipping by default", () => {
    const form = newComponentForm();
    expect(form.kind).toBe("service");
    expect(form.requires_preparation).toBe(true);
});

test("component cannot be saved before selecting one category", () => {
    let form = { ...newComponentForm(), name: "قص", code: "CUT" };
    expect(componentFormCanSave(form)).toBe(false);
    form = toggleComponentCategory(form, "plating");
    expect(componentFormCanSave(form)).toBe(true);
});

test("one component may belong to multiple categories", () => {
    let form = newComponentForm();
    form = toggleComponentCategory(form, "plating");
    form = toggleComponentCategory(form, "clothes");
    expect(form.category_ids).toEqual(["plating", "clothes"]);
});

test("switching to a service enables preparation and stock disables it", () => {
    const stock = changeComponentKind(newComponentForm(), "stock_component");
    expect(stock.requires_preparation).toBe(false);
    expect(stock.unit).toBe("piece");
    const service = changeComponentKind(stock, "service");
    expect(service.requires_preparation).toBe(true);
    expect(service.unit).toBe("job");
});
