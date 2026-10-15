function text(value) {
    return String(value ?? "").trim();
}

export function mergePaginatedRows(existing = [], incoming = [], keyForRow) {
    const merged = Array.isArray(existing) ? [...existing] : [];
    const indexes = new Map();

    merged.forEach((row, index) => {
        const key = text(keyForRow?.(row));
        if (key) indexes.set(key, index);
    });

    (Array.isArray(incoming) ? incoming : []).forEach((row) => {
        const key = text(keyForRow?.(row));
        const index = key ? indexes.get(key) : undefined;
        if (index !== undefined) {
            merged[index] = row;
            return;
        }
        if (key) indexes.set(key, merged.length);
        merged.push(row);
    });

    return merged;
}

export function infinitePaginationState({
    pagination = {},
    requestedPage = 1,
    loaded = 0,
} = {}) {
    const page = Math.max(1, Number(requestedPage || pagination.page || 1));
    const pages = Math.max(0, Number(pagination.pages || 0));
    const total = Math.max(0, Number(pagination.total || loaded || 0));
    const hasMore = pages > 0 ? page < pages : loaded < total;
    return { page, pages, total, hasMore };
}
