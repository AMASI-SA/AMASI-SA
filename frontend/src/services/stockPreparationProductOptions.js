function text(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "object") {
        for (const key of ["name", "label", "title", "value", "text"]) {
            const result = text(value?.[key]);
            if (result) return result;
        }
        return "";
    }
    return String(value).trim();
}

function normalized(value) {
    return text(value).normalize("NFKC").toLocaleLowerCase("ar").replace(/\s+/g, " ");
}

function normalizedValues(rawValues) {
    return (Array.isArray(rawValues) ? rawValues : []).map((value, index) => {
        if (value && typeof value === "object") {
            const id = text(value.id || value.value || value.key) || String(index);
            return {
                ...value,
                id,
                name: text(value.name || value.label || value.value) || id,
            };
        }
        const name = text(value);
        return { id: name || String(index), name: name || String(index) };
    });
}

function productFields(rows, source) {
    return (Array.isArray(rows) ? rows : []).flatMap((row, index) => {
        if (!row || typeof row !== "object") return [];
        const name = text(row.name || row.label || row.title);
        if (!name) return [];
        const id = text(row.id || row.field_id || row.key) || String(index);
        const values = normalizedValues(row.values || row.options);
        return [{
            source,
            key: `${source}:${id}`,
            id,
            name,
            type: text(row.type || row.input_type || row.field_type).toLowerCase()
                || (values.length ? "select" : "text"),
            required: Boolean(row.required || row.is_required),
            placeholder: text(row.placeholder),
            values,
        }];
    });
}

export function buildStockPreparationFields(product) {
    return [
        ...productFields(product?.options, "option"),
        ...productFields(product?.custom_fields, "custom_field"),
    ];
}

export function selectedStockPreparationValue(field, rawValue) {
    if (!field) return "";
    if ((field.values || []).length) {
        const selected = field.values.find((row) => String(row.id) === String(rawValue));
        return text(selected?.name);
    }
    return text(rawValue);
}

export function missingRequiredStockPreparationFields(fields, selections) {
    return (fields || []).filter(
        (field) => field.required && !selectedStockPreparationValue(field, selections?.[field.key]),
    );
}

export function buildStockPreparationSpecifications(fields, selections) {
    return (fields || []).flatMap((field) => {
        const value = selectedStockPreparationValue(field, selections?.[field.key]);
        return value ? [{ name: field.name, value }] : [];
    });
}

function selectionPairs(variant) {
    const source = variant?.selections || variant?.options || variant?.values || variant?.attributes;
    if (source && !Array.isArray(source) && typeof source === "object") {
        return Object.entries(source).map(([option, value]) => ({
            option: text(option),
            value: text(value),
        }));
    }
    return (Array.isArray(source) ? source : []).flatMap((row) => {
        if (!row || typeof row !== "object") {
            return text(row) ? [{ option: "", value: text(row) }] : [];
        }
        const optionObject = row.option && typeof row.option === "object" ? row.option : {};
        const valueObject = row.value && typeof row.value === "object" ? row.value : {};
        const option = text(
            row.option_name
            || row.name
            || row.option_id
            || row.option
            || optionObject.name
            || optionObject.id,
        );
        const value = text(
            row.value_name
            || valueObject.name
            || valueObject.id
            || row.value
            || row.label
            || row.value_id,
        );
        return option || value ? [{ option, value }] : [];
    });
}

function fixedSelection(field, selections) {
    if (field.source !== "option" || !(field.values || []).length) return null;
    const selected = (field.values || []).find(
        (row) => String(row.id) === String(selections?.[field.key] ?? ""),
    );
    if (!selected) return null;
    return {
        optionAliases: new Set([normalized(field.id), normalized(field.name)].filter(Boolean)),
        valueAliases: new Set([normalized(selected.id), normalized(selected.name)].filter(Boolean)),
        valueName: selected.name,
    };
}

function pairMatches(pair, selection) {
    const option = normalized(pair.option);
    const value = normalized(pair.value);
    return selection.valueAliases.has(value)
        && (!option || selection.optionAliases.has(option));
}

export function findStockPreparationVariant(product, fields, selections) {
    const variants = Array.isArray(product?.variants) ? product.variants : [];
    if (!variants.length) return null;
    const fixed = (fields || []).map((field) => fixedSelection(field, selections)).filter(Boolean);
    const fixedFieldCount = (fields || []).filter(
        (field) => field.source === "option" && (field.values || []).length,
    ).length;
    if (fixed.length < fixedFieldCount) return null;
    if (!fixed.length) return variants.length === 1 ? variants[0] : null;

    let matches = variants.filter((variant) => {
        const pairs = selectionPairs(variant);
        return pairs.length && fixed.every(
            (selection) => pairs.some((pair) => pairMatches(pair, selection)),
        );
    });
    if (matches.length === 1) return matches[0];

    matches = variants.filter((variant) => {
        const label = normalized(
            variant.display_name || variant.name || variant.title,
        );
        return label && fixed.every(
            (selection) => label.includes(normalized(selection.valueName)),
        );
    });
    return matches.length === 1 ? matches[0] : null;
}
