export const PRODUCT_TYPE_LABELS = {
    product: "منتج جاهز",
    service: "خدمة حسب الطلب",
};

export const CHILD_SIZE_VALUES = ["سنة", "سنتان", ...Array.from({ length: 8 }, (_, index) => `${index + 3} سنوات`)];

export function newCreationOption(type) {
    return { name: "", type, required: false, description: "", condition: null, values: type === "choice" ? ["", ""] : [] };
}

export function creationOptionsFromDraft(options = []) {
    return options.map((option) => ({ ...option, description: option.description || "", condition: option.condition || null, values: [...(option.values || [])] }));
}

export function serializeCreationOptions(options = []) {
    return options.map((option) => {
        const result = {
            name: option.name.trim(), type: option.type, required: Boolean(option.required),
            values: option.type === "choice" ? option.values.map((value) => value.trim()) : [],
        };
        if (option.description?.trim()) result.description = option.description.trim();
        if (option.type === "choice" && option.value_prices?.length) {
            result.value_prices = option.values.map((_, index) => Number(option.value_prices[index] || 0));
        }
        if (option.condition) result.condition = { ...option.condition, option_index: Number(option.condition.option_index), value: option.condition.value.trim() };
        return result;
    });
}

export function removeCreationOption(options, index) {
    return options.filter((_, position) => position !== index).map((option) => {
        if (!option.condition) return option;
        const parent = Number(option.condition.option_index);
        return { ...option, condition: { ...option.condition, option_index: parent === index ? -1 : parent > index ? parent - 1 : parent } };
    });
}

export function creationOptionVisible(options, index, answers, seen = new Set()) {
    if (seen.has(index) || !options[index]) return false;
    const condition = options[index].condition;
    if (!condition) return true;
    const parent = Number(condition.option_index);
    const value = answers[parent];
    return Boolean(value) && creationOptionVisible(options, parent, answers, new Set([...seen, index]))
        && (condition.operator === "!=" ? value !== condition.value : value === condition.value);
}

export function creationOptionsError(options = []) {
    if (options.length > 20) return "يمكن إضافة 20 خيارًا كحد أقصى.";
    const names = new Set();
    const normalized = serializeCreationOptions(options);
    for (const [index, option] of normalized.entries()) {
        const name = option.name.toLocaleLowerCase();
        if (!name || name.length > 100 || names.has(name)) return "أدخل اسمًا مختلفًا لكل خيار، بحد أقصى 100 حرف.";
        names.add(name);
        if ((option.description || "").length > 1000) return "الوصف التوضيحي بحد أقصى 1000 حرف.";
        if (!["text", "choice"].includes(option.type)) return "اختر حقل نص أو قائمة اختيارات.";
        if (option.value_prices?.some((price) => !Number.isFinite(price) || price < 0 || price > 100000000)) return "السعر الإضافي يجب أن يكون صفرًا أو مبلغًا موجبًا.";
        if (option.type === "choice" && (
            option.values.length < 2 || option.values.length > 100
            || option.values.some((value) => !value || value.length > 100)
            || new Set(option.values.map((value) => value.toLocaleLowerCase())).size !== option.values.length
        )) return "أدخل قيمتين مختلفتين على الأقل لكل قائمة، وبحد أقصى 100 قيمة من 100 حرف.";
        if (option.condition) {
            const parent = option.condition.option_index;
            if (!Number.isInteger(parent) || parent < 0 || parent === index || !normalized[parent]
                || normalized[parent].type !== "choice" || !normalized[parent].values.includes(option.condition.value)
                || !["=", "!="].includes(option.condition.operator)) return "أكمل شرط الظهور باختيار حقل قائمة وقيمة موجودة فيه.";
        }
    }
    for (const [index] of normalized.entries()) {
        const seen = new Set([index]);
        let current = normalized[index];
        while (current.condition) {
            const parent = current.condition.option_index;
            if (seen.has(parent)) return "شروط الظهور لا يمكن أن تعتمد على بعضها في حلقة.";
            seen.add(parent);
            current = normalized[parent];
        }
    }
    return "";
}
