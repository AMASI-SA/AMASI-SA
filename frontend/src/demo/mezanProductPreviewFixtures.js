/*
 * Mezan OS products workspace preview.
 *
 * AMS10026 is a manually verified Salla reference assembled from the product
 * administration screen and an order-line snapshot supplied by the owner.
 * It is not a live products.read response. Customer-uploaded media and the
 * unmasked custom name are intentionally not committed to this public repo.
 */

export const MEZAN_PRODUCT_PREVIEW_META = {
    mode: "manual_salla_reference",
    label: "منتج حقيقي موثّق من شاشات سلة — غير متزامن آليًا بعد",
    salla_scope: "products.read",
    reference_sku: "AMS10026",
    writes_enabled: false,
    privacy_note: "صورة العميل والنص الكامل لا يُحفظان داخل Fixtures العامة.",
};

const money = (amount) => ({ amount, currency: "SAR" });

export const MEZAN_PRODUCT_PREVIEW_FIXTURES = [
    {
        id: "salla-reference-ams10026",
        salla_id: null,
        sku: "AMS10026",
        name: "تعليقة سيارة بالصورة حسب الطلب",
        description: "منتج مخصص موثّق من شاشة إدارة سلة ومن تفاصيل بند طلب فعلي.",
        type: "product",
        status: "sale",
        is_available: true,
        source: "manual_salla_reference",
        source_note: "القيم الظاهرة موثّقة بالصور؛ معرّف سلة والصورة الأصلية ينتظران products.read.",
        salla_snapshot_complete: false,
        thumbnail: "",
        main_image: "",
        price: money(100),
        taxed_price: money(100),
        pre_tax_price: null,
        tax: null,
        sale_price: money(0),
        cost_price: null,
        private_reference: {
            cost_price_present_in_salla: true,
            cost_price_value_committed: false,
        },
        quantity: null,
        unlimited_quantity: true,
        notify_quantity: null,
        require_shipping: true,
        weight: 0.1,
        weight_type: "kg",
        categories: [{ id: "manual-category-car-accessories", name: "اكسسوارات السيارة" }],
        options: [
            {
                id: "ams10026-customer-name",
                key: "customer_name",
                name: "الاسم",
                type: "text",
                display_type: "text",
                required: true,
                placeholder: "هنا",
                values: [],
                creates_variant: false,
            },
            {
                id: "ams10026-customer-image",
                key: "customer_image",
                name: "اسحب وأفلت الصورة هنا",
                type: "image",
                display_type: "file",
                required: true,
                placeholder: "استعراضي",
                values: [],
                creates_variant: false,
            },
            {
                id: "ams10026-color",
                key: "color",
                name: "اللون",
                type: "radio",
                display_type: "text",
                required: true,
                placeholder: "اختر",
                values: [
                    {
                        id: "ams10026-color-gold",
                        key: "gold",
                        name: "ذهبي",
                        price: money(0),
                        is_default: true,
                        is_out_of_stock: false,
                    },
                    {
                        id: "ams10026-color-silver",
                        key: "silver",
                        name: "فضي",
                        price: money(0),
                        is_default: false,
                        is_out_of_stock: false,
                    },
                ],
            },
        ],
        variants: [
            {
                id: "ams10026-base-sku",
                sku: "AMS10026",
                price: money(100),
                sale_price: money(0),
                stock_quantity: null,
                stock_mode: "unlimited_in_salla",
                related_option_values: [],
            },
        ],
    },
];

export const MEZAN_COST_RESOURCES_FIXTURES = [
    {
        id: "component-packaging-bag",
        code: "PKG-BAG",
        name: "كيس",
        kind: "stock_component",
        unit: "piece",
        unit_cost: null,
        track_inventory: true,
        inventory: { on_hand: null, reserved: 0 },
        source: "manual_mezan_catalog",
    },
    {
        id: "component-packaging-box",
        code: "PKG-BOX",
        name: "علبة",
        kind: "stock_component",
        unit: "piece",
        unit_cost: null,
        track_inventory: true,
        inventory: { on_hand: null, reserved: 0 },
        source: "manual_mezan_catalog",
    },
    {
        id: "component-chain-silver",
        code: "CHAIN-SILVER",
        name: "سلسال فضي",
        kind: "stock_component",
        unit: "piece",
        unit_cost: null,
        track_inventory: true,
        inventory: { on_hand: null, reserved: 0 },
        source: "manual_mezan_catalog",
    },
    {
        id: "component-chain-gold",
        code: "CHAIN-GOLD",
        name: "سلسال ذهبي",
        kind: "stock_component",
        unit: "piece",
        unit_cost: null,
        track_inventory: true,
        inventory: { on_hand: null, reserved: 0 },
        source: "manual_mezan_catalog",
    },
    {
        id: "service-engraving",
        code: "LABOR-ENGRAVING",
        name: "نحت",
        kind: "labor_service",
        unit: "job",
        unit_cost: null,
        track_inventory: false,
        inventory: null,
        source: "manual_mezan_catalog",
    },
    {
        id: "service-plating",
        code: "LABOR-PLATING",
        name: "طلاء",
        kind: "labor_service",
        unit: "job",
        unit_cost: null,
        track_inventory: false,
        inventory: null,
        source: "manual_mezan_catalog",
    },
    {
        id: "service-assembly",
        code: "LABOR-ASSEMBLY",
        name: "تركيب",
        kind: "labor_service",
        unit: "job",
        unit_cost: null,
        track_inventory: false,
        inventory: null,
        source: "manual_mezan_catalog",
    },
];

export const MEZAN_PRODUCT_RECIPE_FIXTURES = [
    {
        id: "recipe-ams10026-v1",
        product_id: "salla-reference-ams10026",
        version: 1,
        status: "draft_preview",
        base_lines: [
            { resource_id: "component-packaging-bag", quantity: 1, reason: "تغليف أساسي" },
            { resource_id: "component-packaging-box", quantity: 1, reason: "تغليف أساسي" },
            { resource_id: "service-engraving", quantity: 1, reason: "تنفيذ الاسم والصورة" },
            { resource_id: "service-assembly", quantity: 1, reason: "تجميع المنتج" },
        ],
        option_rules: [
            {
                id: "ams10026-color-gold-rule",
                when: { option_key: "color", value_key: "gold" },
                effects: [
                    { type: "add_component", resource_id: "component-chain-gold", quantity: 1 },
                ],
            },
            {
                id: "ams10026-color-silver-rule",
                when: { option_key: "color", value_key: "silver" },
                effects: [
                    { type: "add_component", resource_id: "component-chain-silver", quantity: 1 },
                    {
                        type: "fixed_cost_delta",
                        amount: 5,
                        label: "فرق تكلفة داخلي للون الفضي",
                        source: "owner_rule",
                    },
                ],
            },
        ],
        configuration_policy: {
            normalized_text: "NFKC_TRIM",
            attachment_affects_ready_stock_key: false,
            attachment_note: "الصورة محفوظة مع الطلب، ولا تدخل في مفتاح المخزون الجاهز في هذه المعاينة.",
        },
    },
];

export const MEZAN_READY_CONFIGURATION_STOCK_FIXTURES = [
    {
        id: "ready-ams10026-silver-demo-name",
        product_id: "salla-reference-ams10026",
        configuration: {
            option_values: { color: "silver" },
            custom_values: { customer_name: { raw: "اسم تجريبي", normalized: "اسم تجريبي" } },
        },
        configuration_key: "AMS10026|color=silver|name=اسم تجريبي",
        quantity_on_hand: 50,
        quantity_reserved: 0,
        source: "owner_example",
    },
    {
        id: "ready-ams10026-gold-demo-name",
        product_id: "salla-reference-ams10026",
        configuration: {
            option_values: { color: "gold" },
            custom_values: { customer_name: { raw: "اسم تجريبي", normalized: "اسم تجريبي" } },
        },
        configuration_key: "AMS10026|color=gold|name=اسم تجريبي",
        quantity_on_hand: 0,
        quantity_reserved: 0,
        source: "owner_example",
    },
];

export const MEZAN_ORDER_LINE_PREVIEW_FIXTURES = [
    {
        id: "order-line-example-ams10026",
        product_id: "salla-reference-ams10026",
        source: "verified_order_screenshot",
        sku: "AMS10026",
        product_name: "تعليقة سيارة بالصورة حسب الطلب",
        quantity: 1,
        weight: 0.1,
        weight_unit: "kg",
        unit_price: money(100),
        total: money(100),
        selected_options: [
            {
                key: "customer_name",
                label: "الاسم",
                value: "ع***",
                value_masked: true,
                private_value_available_at_runtime: true,
            },
            {
                key: "customer_image",
                label: "اسحب وأفلت الصورة هنا",
                attachment_present: true,
                attachment_persisted_in_fixture: false,
            },
            { key: "color", label: "اللون", value: "silver", display_value: "فضي" },
        ],
    },
];
