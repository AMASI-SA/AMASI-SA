const text = (value) => String(value || "").trim();

export function reviewedRemainingQuantity(product) {
    const raw = product?.remaining_quantity ?? product?.quantity ?? 0;
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0) return 0;
    return Math.max(0, Math.floor(value));
}

export function clampReviewedPreparationQuantity(product, value) {
    const remaining = reviewedRemainingQuantity(product);
    if (remaining <= 0) return 0;
    if (product?.piece_level) return 1;
    const number = Number(value);
    if (!Number.isFinite(number)) return 1;
    return Math.min(remaining, Math.max(1, Math.floor(number)));
}

export function toggleReviewedPreparationProduct(current, product) {
    const groupKey = text(product?.group_key);
    if (!groupKey) return { ...(current || {}) };
    const next = { ...(current || {}) };
    if (Object.prototype.hasOwnProperty.call(next, groupKey)) {
        delete next[groupKey];
        return next;
    }
    const remaining = reviewedRemainingQuantity(product);
    if (remaining > 0) next[groupKey] = product?.piece_level ? 1 : remaining;
    return next;
}

export function setReviewedPreparationQuantity(current, product, value) {
    const groupKey = text(product?.group_key);
    if (!groupKey) return { ...(current || {}) };
    const next = { ...(current || {}) };
    const quantity = clampReviewedPreparationQuantity(product, value);
    if (quantity <= 0) delete next[groupKey];
    else next[groupKey] = quantity;
    return next;
}

export function reconcileReviewedPreparationSelection(current, products) {
    const productByKey = new Map(
        (products || [])
            .filter((product) => text(product?.group_key))
            .map((product) => [text(product.group_key), product]),
    );
    const next = {};
    Object.entries(current || {}).forEach(([groupKey, quantity]) => {
        const product = productByKey.get(groupKey);
        if (!product) return;
        const clamped = clampReviewedPreparationQuantity(product, quantity);
        if (clamped > 0) next[groupKey] = clamped;
    });
    return next;
}

export function reviewedPreparationSelectionSummary(current) {
    const entries = Object.entries(current || {})
        .map(([group_key, quantity]) => ({
            group_key: text(group_key),
            quantity: Math.max(0, Math.floor(Number(quantity) || 0)),
        }))
        .filter((row) => row.group_key && row.quantity > 0)
        .sort((left, right) => left.group_key.localeCompare(right.group_key));
    return {
        selections: entries,
        productCount: entries.length,
        totalQuantity: entries.reduce((total, row) => total + row.quantity, 0),
    };
}

export function createPreparationClientRequestId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }
    return `prep-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
