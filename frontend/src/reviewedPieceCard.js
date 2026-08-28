const text = (value) => String(value ?? "").trim();

function optionValue(value) {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) {
        return value.map(optionValue).filter(Boolean).join("، ");
    }
    if (typeof value === "object") {
        for (const key of ["value", "label", "name", "title", "text"]) {
            const nested = optionValue(value[key]);
            if (nested) return nested;
        }
        return Object.values(value).map(optionValue).filter(Boolean).join("، ");
    }
    return text(value);
}

export function reviewedPieceOrderNumber(product) {
    const lineOrder = product?.source_lines?.[0]?.order_number;
    return text(lineOrder || product?.source_order_numbers?.[0]);
}

export function reviewedPieceCustomerOptions(product) {
    const raw = product?.source_lines?.[0]?.options_normalized;
    if (Array.isArray(raw)) {
        return raw.map((row, index) => {
            if (!row || typeof row !== "object") return null;
            const label = text(row.name || row.label || row.key || `الخيار ${index + 1}`);
            const value = optionValue(row.value ?? row.selected ?? row.text);
            return label && value ? { label, value } : null;
        }).filter(Boolean);
    }
    if (!raw || typeof raw !== "object") return [];
    return Object.entries(raw).map(([key, value]) => ({
        label: text(key),
        value: optionValue(value),
    })).filter((row) => row.label && row.value);
}
