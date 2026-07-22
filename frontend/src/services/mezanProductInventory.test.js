import {
    MEZAN_INVENTORY_CONFIGURATION_FIXTURES,
    MEZAN_INVENTORY_MOVEMENT_FIXTURES,
    MEZAN_INVENTORY_RESERVATION_FIXTURES,
    MEZAN_STORAGE_LOCATION_FIXTURES,
} from "../demo/mezanProductPreviewFixtures";
import {
    createPersonalizedConfiguration,
    findReadyStockForOrder,
    formatStorageLocation,
    getConfigurationBalance,
    receiveApprovedReturnPreview,
    receivePurchasePreview,
    transformStockPreview,
} from "./mezanProductInventory";

function initialState() {
    return JSON.parse(JSON.stringify({
        configurations: MEZAN_INVENTORY_CONFIGURATION_FIXTURES,
        locations: MEZAN_STORAGE_LOCATION_FIXTURES,
        movements: MEZAN_INVENTORY_MOVEMENT_FIXTURES,
        reservations: MEZAN_INVENTORY_RESERVATION_FIXTURES,
    }));
}

function productionInput(overrides = {}) {
    return {
        idempotency_key: "production:MO-PREVIEW-001",
        production_reference: "MO-PREVIEW-001",
        source_configuration_id: "config-ams10026-base-silver",
        source_location_id: "location-main-c01-r01-b01",
        source_lot_id: "lot-purchase-silver-001",
        destination_configuration: createPersonalizedConfiguration({
            productId: "salla-reference-ams10026",
            sku: "AMS10026",
            color: "silver",
            customerName: "اسم تجريبي",
            attachmentFingerprint: "IMG-PREVIEW-001",
        }),
        destination_location_id: "location-main-c02-r01-b01",
        destination_lot_id: "lot-production-preview-001",
        quantity_units: 20,
        requires_attachment: true,
        attachment_present: true,
        attachment_fingerprint: "IMG-PREVIEW-001",
        ...overrides,
    };
}

test("posted purchase invoices create fifty silver and one hundred gold at complete locations", () => {
    const state = initialState();
    expect(getConfigurationBalance(state, "config-ams10026-base-silver", { condition: "sellable" })).toBe(50);
    expect(getConfigurationBalance(state, "config-ams10026-base-gold", { condition: "sellable" })).toBe(100);
    expect(formatStorageLocation(state.locations[0])).toBe("المخزن 01 · العمود 01 · الصف 01 · الخانة 01");
});

test("production consumes twenty general silver and creates twenty matching personalized units atomically", () => {
    const result = transformStockPreview(initialState(), productionInput());
    expect(result.ok).toBe(true);
    const readyId = result.state.configurations.find((entry) => (
        entry.configuration_key === "AMS10026|stage=personalized_ready|color=silver|name=اسم تجريبي|image=IMG-PREVIEW-001"
    )).id;
    expect(getConfigurationBalance(result.state, "config-ams10026-base-silver", { condition: "sellable" })).toBe(30);
    expect(getConfigurationBalance(result.state, readyId, { condition: "sellable" })).toBe(20);
    expect(30 + 20).toBe(50);
});

test("replaying the same production reference does not consume stock twice", () => {
    const first = transformStockPreview(initialState(), productionInput());
    const replay = transformStockPreview(first.state, productionInput());
    expect(replay.ok).toBe(true);
    expect(replay.duplicate).toBe(true);
    expect(getConfigurationBalance(replay.state, "config-ams10026-base-silver", { condition: "sellable" })).toBe(30);
});

test("production rejects an amount larger than the remaining source stock without partial output", () => {
    const first = transformStockPreview(initialState(), productionInput());
    const rejected = transformStockPreview(first.state, productionInput({
        idempotency_key: "production:MO-PREVIEW-002",
        production_reference: "MO-PREVIEW-002",
        destination_lot_id: "lot-production-preview-002",
        quantity_units: 40,
    }));
    expect(rejected.ok).toBe(false);
    expect(rejected.error.code).toBe("insufficient_stock");
    expect(getConfigurationBalance(rejected.state, "config-ams10026-base-silver", { condition: "sellable" })).toBe(30);
});

test("production cannot change the color while transforming a base unit", () => {
    const destination = createPersonalizedConfiguration({
        productId: "salla-reference-ams10026",
        sku: "AMS10026",
        color: "gold",
        customerName: "اسم تجريبي",
        attachmentFingerprint: "IMG-PREVIEW-001",
    });
    const rejected = transformStockPreview(initialState(), productionInput({ destination_configuration: destination }));
    expect(rejected.ok).toBe(false);
    expect(rejected.error.code).toBe("invalid_configuration_transition");
});

test("purchase receipt requires a complete storage location", () => {
    const state = initialState();
    state.locations.push({ id: "location-incomplete", warehouse_code: "01", active: true });
    const result = receivePurchasePreview(state, {
        idempotency_key: "purchase:PI-BAD-001",
        invoice_reference: "PI-BAD-001",
        configuration_id: "config-ams10026-base-silver",
        location_id: "location-incomplete",
        quantity_units: 5,
    });
    expect(result.ok).toBe(false);
    expect(result.error.code).toBe("incomplete_storage_location");
});

test("failed production does not leave a newly created personalized configuration behind", () => {
    const destination = createPersonalizedConfiguration({
        productId: "salla-reference-ams10026",
        sku: "AMS10026",
        color: "silver",
        customerName: "اسم جديد",
        attachmentFingerprint: "IMG-NEW-001",
    });
    const state = initialState();
    const result = transformStockPreview(state, productionInput({
        idempotency_key: "production:MO-ATOMIC-FAIL",
        production_reference: "MO-ATOMIC-FAIL",
        destination_configuration: destination,
        attachment_fingerprint: "IMG-NEW-001",
        quantity_units: 60,
    }));
    expect(result.ok).toBe(false);
    expect(result.state).toBe(state);
    expect(result.state.configurations.some((entry) => entry.configuration_key === destination.configuration_key)).toBe(false);
});

test("required personalized image blocks production when its fingerprint is missing", () => {
    const result = transformStockPreview(initialState(), productionInput({
        attachment_present: false,
        attachment_fingerprint: "",
    }));
    expect(result.ok).toBe(false);
    expect(result.error.code).toBe("incomplete_configuration");
});

test("production rejects a fingerprint that differs from the destination configuration", () => {
    const result = transformStockPreview(initialState(), productionInput({
        attachment_fingerprint: "IMG-DIFFERENT-001",
    }));
    expect(result.ok).toBe(false);
    expect(result.error.code).toBe("attachment_mismatch");
});

test("trimmed purchase reference is idempotent and an empty reference is rejected", () => {
    const input = {
        invoice_reference: " PI-TRIM-001 ",
        configuration_id: "config-ams10026-base-silver",
        location_id: "location-main-c01-r01-b01",
        lot_id: "lot-purchase: PI-TRIM-001 ",
        quantity_units: 5,
    };
    const first = receivePurchasePreview(initialState(), input);
    const replay = receivePurchasePreview(first.state, { ...input, invoice_reference: "PI-TRIM-001" });
    const empty = receivePurchasePreview(initialState(), { ...input, invoice_reference: "   " });
    expect(first.ok).toBe(true);
    expect(replay.ok).toBe(true);
    expect(replay.duplicate).toBe(true);
    expect(empty.ok).toBe(false);
    expect(empty.error.code).toBe("missing_reference");
});

test("purchase cost accepts null or non-negative integer halalas and rejects unsafe values", () => {
    const base = {
        invoice_reference: "PI-COST-001",
        configuration_id: "config-ams10026-base-silver",
        location_id: "location-main-c01-r01-b01",
        quantity_units: 1,
    };
    expect(receivePurchasePreview(initialState(), { ...base, unit_cost_halalas: null }).ok).toBe(true);
    expect(receivePurchasePreview(initialState(), { ...base, invoice_reference: "PI-COST-002", unit_cost_halalas: 525 }).ok).toBe(true);
    expect(receivePurchasePreview(initialState(), { ...base, invoice_reference: "PI-COST-003", unit_cost_halalas: -1 }).error.code).toBe("invalid_unit_cost");
    expect(receivePurchasePreview(initialState(), { ...base, invoice_reference: "PI-COST-004", unit_cost_halalas: Number.NaN }).error.code).toBe("invalid_unit_cost");
});

test("approved return increases the exact configuration only at its selected location", () => {
    const produced = transformStockPreview(initialState(), productionInput());
    const ready = produced.state.configurations.find((entry) => entry.stage === "personalized_ready");
    const result = receiveApprovedReturnPreview(produced.state, {
        idempotency_key: "return:RET-PREVIEW-001",
        return_reference: "RET-PREVIEW-001",
        order_reference: "ORDER-PREVIEW-001",
        configuration_id: ready.id,
        location_id: "location-returns-c01-r01-b01",
        quantity_units: 1,
        approved_for_stock: true,
    });
    expect(result.ok).toBe(true);
    expect(getConfigurationBalance(result.state, ready.id, { condition: "sellable" })).toBe(21);
});

test("return is blocked until review approves keeping it as stock", () => {
    const result = receiveApprovedReturnPreview(initialState(), {
        idempotency_key: "return:RET-PREVIEW-002",
        return_reference: "RET-PREVIEW-002",
        order_reference: "ORDER-PREVIEW-002",
        configuration_id: "config-ams10026-personalized-silver-demo",
        location_id: "location-returns-c01-r01-b01",
        quantity_units: 1,
        approved_for_stock: false,
    });
    expect(result.ok).toBe(false);
    expect(result.error.code).toBe("return_not_approved");
});

test("approved return requires its original order and required image fingerprint", () => {
    const baseInput = {
        return_reference: "RET-PREVIEW-003",
        order_reference: "ORDER-PREVIEW-003",
        configuration_id: "config-ams10026-personalized-silver-demo",
        location_id: "location-returns-c01-r01-b01",
        quantity_units: 1,
        approved_for_stock: true,
        requires_attachment: true,
    };
    const missingOrder = receiveApprovedReturnPreview(initialState(), {
        ...baseInput,
        order_reference: "   ",
    });
    const stateWithoutFingerprint = initialState();
    stateWithoutFingerprint.configurations = stateWithoutFingerprint.configurations.map((entry) => (
        entry.id === baseInput.configuration_id
            ? { ...entry, custom_values: { ...entry.custom_values, customer_image: null } }
            : entry
    ));
    const missingImage = receiveApprovedReturnPreview(stateWithoutFingerprint, baseInput);
    expect(missingOrder.ok).toBe(false);
    expect(missingOrder.error.code).toBe("missing_order_reference");
    expect(missingImage.ok).toBe(false);
    expect(missingImage.error.code).toBe("incomplete_configuration");
});

test("matching order specifications returns the available quantity and exact warehouse coordinates", () => {
    const produced = transformStockPreview(initialState(), productionInput());
    const match = findReadyStockForOrder(produced.state, {
        productId: "salla-reference-ams10026",
        sku: "AMS10026",
        color: "silver",
        customerName: " اسم تجريبي ",
        attachmentFingerprint: "IMG-PREVIEW-001",
    });
    expect(match.matched).toBe(true);
    expect(match.quantity_available).toBe(20);
    expect(match.locations[0].location_label).toBe("المخزن 01 · العمود 02 · الصف 01 · الخانة 01");
});
