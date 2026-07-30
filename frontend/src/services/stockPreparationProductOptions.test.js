import {
    buildStockPreparationFields,
    buildStockPreparationSpecifications,
    findStockPreparationVariant,
    missingRequiredStockPreparationFields,
} from "./stockPreparationProductOptions";

const product = {
    options: [{
        id: "color",
        name: "اللون",
        type: "select",
        required: true,
        values: [
            { id: "gold", name: "ذهبي" },
            { id: "silver", name: "فضي" },
        ],
    }],
    custom_fields: [{
        id: "customer-name",
        name: "الاسم",
        type: "text",
        required: true,
    }],
    variants: [
        {
            id: "variant-gold",
            display_name: "ذهبي",
            selections: [{ option_name: "اللون", value_name: "ذهبي" }],
        },
        {
            id: "variant-silver",
            display_name: "فضي",
            selections: [{ option_name: "اللون", value_name: "فضي" }],
        },
    ],
};

test("builds only the Salla fields configured on each product", () => {
    const fields = buildStockPreparationFields(product);

    expect(fields.map((field) => [field.name, field.type])).toEqual([
        ["اللون", "select"],
        ["الاسم", "text"],
    ]);
});

test("builds specifications and matches the Salla variant", () => {
    const fields = buildStockPreparationFields(product);
    const selections = {
        "option:color": "gold",
        "custom_field:customer-name": "عبير",
    };

    expect(buildStockPreparationSpecifications(fields, selections)).toEqual([
        { name: "اللون", value: "ذهبي" },
        { name: "الاسم", value: "عبير" },
    ]);
    expect(findStockPreparationVariant(product, fields, selections)?.id).toBe("variant-gold");
    expect(missingRequiredStockPreparationFields(fields, selections)).toEqual([]);
});

test("supports products whose configured fields are size and name", () => {
    const sizeAndName = {
        options: [{
            id: "size",
            name: "المقاس",
            values: [{ id: "45", name: "45 سم" }],
        }],
        custom_fields: [{
            id: "name",
            name: "الاسم",
            type: "text",
            required: true,
        }],
        variants: [{
            id: "size-45",
            selections: [{ option_id: "size", value_id: "45" }],
        }],
    };
    const fields = buildStockPreparationFields(sizeAndName);
    const selections = {
        "option:size": "45",
        "custom_field:name": "سارة",
    };

    expect(buildStockPreparationSpecifications(fields, selections)).toEqual([
        { name: "المقاس", value: "45 سم" },
        { name: "الاسم", value: "سارة" },
    ]);
    expect(findStockPreparationVariant(sizeAndName, fields, selections)?.id).toBe("size-45");
});

test("reports missing required Salla fields", () => {
    const fields = buildStockPreparationFields(product);

    expect(
        missingRequiredStockPreparationFields(fields, { "option:color": "gold" })
            .map((field) => field.name),
    ).toEqual(["الاسم"]);
});
