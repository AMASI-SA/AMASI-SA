export function riyadhDateParts(date = new Date()) {
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).formatToParts(date);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    const year = Number(values.year);
    const month = Number(values.month);
    const day = Number(values.day);
    return {
        iso: `${values.year}-${values.month}-${values.day}`,
        display: `${year}/${month}/${day}`,
    };
}

export function readSelectionMetric(root, label) {
    const node = [...(root?.querySelectorAll?.("div") || [])].find(
        (entry) => String(entry.textContent || "").trim() === label,
    );
    const value = Number(String(node?.nextElementSibling?.textContent || "").trim());
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

export function readSelectedProductCount(root) {
    return readSelectionMetric(root, "البطاقات المحددة")
        || readSelectionMetric(root, "المنتجات المحددة");
}

export function preparationFileMetadataPayload({
    fileTitle,
    responsibleEmployeeId,
    expectedQuantity,
    selectedProductCount,
}) {
    return {
        fileTitle: String(fileTitle || "").trim(),
        responsibleEmployeeId: String(responsibleEmployeeId || "").trim(),
        expectedQuantity: Math.max(0, Math.floor(Number(expectedQuantity) || 0)),
        selectedProductCount: Math.max(0, Math.floor(Number(selectedProductCount) || 0)),
    };
}

export function preparationFileRecordLabel(file) {
    const number = String(file?.file_number || "").trim();
    const title = String(file?.file_title || file?.file_name || "ملف تجهيز").trim();
    return number ? `${number} — ${title}` : title;
}
