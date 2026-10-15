export function newComponentForm() {
    return {
        name: "",
        code: "",
        kind: "service",
        unit: "job",
        unit_cost: "",
        description: "",
        requires_preparation: true,
        category_ids: [],
    };
}

export function componentFormFromRow(component) {
    if (!component) return newComponentForm();
    return {
        name: component.name || "",
        code: component.code || "",
        kind: component.track_inventory ? "stock_component" : "service",
        unit: component.unit || (component.track_inventory ? "piece" : "job"),
        unit_cost: component.track_inventory
            ? (component.initial_unit_cost ?? component.reference_cost?.amount ?? "")
            : (component.reference_cost?.amount ?? ""),
        description: component.description || "",
        requires_preparation: !component.track_inventory && component.requires_preparation === true,
        category_ids: (component.category_ids || []).map(String),
    };
}

export function changeComponentKind(form, kind) {
    const stock = kind === "stock_component";
    return {
        ...form,
        kind,
        unit: stock ? "piece" : "job",
        requires_preparation: stock ? false : true,
    };
}

export function toggleComponentCategory(form, categoryId) {
    const value = String(categoryId);
    const current = (form.category_ids || []).map(String);
    return {
        ...form,
        category_ids: current.includes(value)
            ? current.filter((item) => item !== value)
            : [...current, value],
    };
}

export function normalizeComponentName(value) {
    return String(value || "")
        .normalize("NFKC")
        .toLocaleLowerCase("ar")
        .replace(/\s+/g, " ")
        .trim();
}

export function componentNameAlreadyExists(components, name, excludeId = "") {
    const target = normalizeComponentName(name);
    const excluded = String(excludeId || "");
    if (!target) return false;
    return (components || []).some((component) => (
        String(component?.id || "") !== excluded
        && normalizeComponentName(component?.name) === target
    ));
}

export function componentFormCanSave(form) {
    return Boolean(
        form?.name?.trim()
        && form?.code?.trim()
        && (form?.category_ids || []).length > 0
    );
}
