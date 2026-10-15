export const PRODUCT_TYPE_LABELS = {
    product: "منتج جاهز",
    service: "خدمة حسب الطلب",
};

export const CHILD_SIZE_VALUES = ["سنة", "سنتان", ...Array.from({ length: 8 }, (_, index) => `${index + 3} سنوات`)];

export function newCreationOption(type) {
    return { name: "", type, required: false, values_text: "" };
}

export function creationOptionsFromDraft(options = []) {
    return options.map((option) => ({ ...option, values_text: (option.values || []).join("\n") }));
}

export function serializeCreationOptions(options = []) {
    return options.map((option) => ({
        name: option.name.trim(),
        type: option.type,
        required: Boolean(option.required),
        values: option.type === "choice"
            ? option.values_text.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)
            : [],
    }));
}

export function creationOptionsError(options = []) {
    if (options.length > 20) return "يمكن إضافة 20 خيارًا كحد أقصى.";
    const names = new Set();
    for (const option of serializeCreationOptions(options)) {
        const name = option.name.toLocaleLowerCase();
        if (!name || name.length > 100 || names.has(name)) return "أدخل اسمًا مختلفًا لكل خيار، بحد أقصى 100 حرف.";
        names.add(name);
        if (!["text", "choice"].includes(option.type)) return "اختر حقل نص أو قائمة اختيارات.";
        if (option.type === "choice" && (
            option.values.length < 2 || option.values.length > 100
            || option.values.some((value) => value.length > 100)
            || new Set(option.values.map((value) => value.toLocaleLowerCase())).size !== option.values.length
        )) return "أدخل قيمتين مختلفتين على الأقل لكل قائمة، وبحد أقصى 100 قيمة من 100 حرف.";
    }
    return "";
}
