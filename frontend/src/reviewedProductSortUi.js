const text = (value) => String(value || "").trim();

export function reviewedProductSortCandidateSummary(candidate, limit = 3) {
    const values = Array.isArray(candidate?.values) ? candidate.values : [];
    return values
        .slice(0, Math.max(1, Number(limit) || 3))
        .map((row) => `${text(row?.value) || "غير محدد"} (${Math.max(0, Math.floor(Number(row?.quantity) || 0))} قطعة)`)
        .join("، ");
}

export function reviewedProductSortButtonLabel(product) {
    const label = text(product?.preparation_sort_label || product?.preparation_sort_spec);
    return label ? `ترتيب الملف: ${label}` : "تحديد ترتيب الملف";
}

export function reviewedProductCardIdentity(name, sku = "") {
    return `${text(name).toLocaleLowerCase("ar")}::${text(sku).toLocaleLowerCase("en")}`;
}

export function findReviewedProductForCard(products, { name, sku = "" }, usedKeys = new Set()) {
    const wantedName = text(name);
    const wantedSku = text(sku);
    const rows = Array.isArray(products) ? products : [];

    const exact = rows.find((product) => {
        const groupKey = text(product?.group_key);
        if (!groupKey || usedKeys.has(groupKey)) return false;
        const sameName = text(product?.name) === wantedName;
        const productSku = text(product?.sku);
        return sameName && (!wantedSku || productSku === wantedSku);
    });
    if (exact) return exact;

    return rows.find((product) => {
        const groupKey = text(product?.group_key);
        return groupKey && !usedKeys.has(groupKey) && text(product?.name) === wantedName;
    }) || null;
}

export function updateReviewedProductSortPreference(product, result) {
    return {
        ...(product || {}),
        preparation_sort_spec: result?.spec_key || null,
        preparation_sort_label: result?.spec_label || null,
        preparation_sort_candidates: Array.isArray(result?.candidates)
            ? result.candidates
            : (product?.preparation_sort_candidates || []),
    };
}
