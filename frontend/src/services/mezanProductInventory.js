import { normalizeCustomText } from "./mezanProductCosting";

function safeUnits(value) {
    const units = Number(value);
    return Number.isSafeInteger(units) && units > 0 ? units : null;
}

function stableTransactionShape(transaction) {
    return JSON.stringify({
        type: transaction.type,
        reference: transaction.reference || null,
        lines: transaction.lines || [],
        cost_lines: transaction.cost_lines || [],
    });
}

function normalizeAuditReference(value) {
    return String(value ?? "").normalize("NFKC").trim();
}

export function buildInventoryConfigurationKey({ sku, stage, color, customerName, attachmentFingerprint }) {
    const normalizedFingerprint = normalizeAuditReference(attachmentFingerprint);
    const parts = [
        String(sku || "").trim().toUpperCase(),
        `stage=${String(stage || "").trim().toLowerCase()}`,
        `color=${String(color || "").trim().toLowerCase()}`,
    ];
    if (stage === "personalized_ready") {
        parts.push(`name=${normalizeCustomText(customerName)}`);
        if (normalizedFingerprint) {
            parts.push(`image=${normalizedFingerprint}`);
        }
    }
    return parts.join("|");
}

export function buildInventoryBucketKey({ configuration_id, location_id, condition, lot_id }) {
    return [configuration_id, location_id, condition || "sellable", lot_id || "NO_LOT"].join("|");
}

export function formatStorageLocation(location) {
    if (!location) return "موقع غير معروف";
    return [
        `المخزن ${location.warehouse_code}`,
        `العمود ${location.column_code}`,
        `الصف ${location.row_code}`,
        `الخانة ${location.bin_code}`,
    ].join(" · ");
}

export function isCompleteStorageLocation(location) {
    return Boolean(
        location?.active
        && location.warehouse_code
        && location.column_code
        && location.row_code
        && location.bin_code
    );
}

export function deriveInventoryBalances(state) {
    const buckets = new Map();
    const movements = state?.movements || [];

    for (const movement of movements) {
        if (movement.status !== "posted") continue;
        for (const line of movement.lines || []) {
            const key = buildInventoryBucketKey(line);
            const current = buckets.get(key) || {
                bucket_key: key,
                configuration_id: line.configuration_id,
                location_id: line.location_id,
                condition: line.condition || "sellable",
                lot_id: line.lot_id || null,
                quantity_on_hand: 0,
                quantity_reserved: 0,
            };
            current.quantity_on_hand += Number(line.delta_units || 0);
            buckets.set(key, current);
        }
    }

    for (const reservation of state?.reservations || []) {
        if (reservation.status !== "active") continue;
        const bucket = buckets.get(reservation.bucket_key);
        if (bucket) bucket.quantity_reserved += Number(reservation.quantity_units || 0);
    }

    return [...buckets.values()].map((bucket) => ({
        ...bucket,
        quantity_available: bucket.quantity_on_hand - bucket.quantity_reserved,
        valid: bucket.quantity_on_hand >= 0
            && bucket.quantity_reserved >= 0
            && bucket.quantity_reserved <= bucket.quantity_on_hand,
    }));
}

export function getConfigurationBalance(state, configurationId, filters = {}) {
    const rows = deriveInventoryBalances(state).filter((row) => (
        row.configuration_id === configurationId
        && (!filters.location_id || row.location_id === filters.location_id)
        && (!filters.condition || row.condition === filters.condition)
        && (!filters.lot_id || row.lot_id === filters.lot_id)
    ));
    return rows.reduce((sum, row) => sum + row.quantity_available, 0);
}

function validateStateCatalog(state) {
    const configurationKeys = new Set();
    for (const configuration of state?.configurations || []) {
        if (configurationKeys.has(configuration.configuration_key)) {
            return { code: "duplicate_configuration", message: "يوجد تكوين مخزني مكرر." };
        }
        configurationKeys.add(configuration.configuration_key);
    }
    return null;
}

function validateTransaction(state, transaction) {
    const catalogError = validateStateCatalog(state);
    if (catalogError) return catalogError;
    if (!transaction?.idempotency_key) {
        return { code: "missing_idempotency_key", message: "رقم العملية الفريد إلزامي." };
    }
    if (!transaction.lines?.length) {
        return { code: "missing_lines", message: "لا توجد أسطر حركة مخزنية." };
    }

    const configurationIds = new Set((state.configurations || []).map((entry) => entry.id));
    const locations = new Map((state.locations || []).map((entry) => [entry.id, entry]));
    for (const line of transaction.lines) {
        if (!configurationIds.has(line.configuration_id)) {
            return { code: "unknown_configuration", message: "التكوين المخزني غير معروف." };
        }
        if (!isCompleteStorageLocation(locations.get(line.location_id))) {
            return { code: "incomplete_storage_location", message: "يجب تحديد المخزن والعمود والصف والخانة." };
        }
        if (!Number.isSafeInteger(Number(line.delta_units)) || Number(line.delta_units) === 0) {
            return { code: "invalid_quantity", message: "الكمية يجب أن تكون عددًا صحيحًا غير صفري." };
        }
    }

    if (transaction.type === "stock_transform") {
        const consumed = transaction.lines
            .filter((line) => line.delta_units < 0)
            .reduce((sum, line) => sum + Math.abs(line.delta_units), 0);
        const produced = transaction.lines
            .filter((line) => line.delta_units > 0)
            .reduce((sum, line) => sum + line.delta_units, 0);
        if (consumed !== produced) {
            return { code: "unbalanced_transform", message: "كمية التحويل المستهلكة لا تساوي الكمية المنتجة." };
        }
    }
    return null;
}

export function postInventoryTransaction(state, transaction) {
    const normalizedTransaction = {
        ...transaction,
        idempotency_key: normalizeAuditReference(transaction?.idempotency_key),
        reference: transaction?.reference ? {
            ...transaction.reference,
            id: normalizeAuditReference(transaction.reference.id),
        } : null,
    };
    if (!normalizedTransaction.reference?.id) {
        return {
            ok: false,
            state,
            error: { code: "missing_reference", message: "مرجع المستند إلزامي ولا يمكن أن يكون فارغًا." },
        };
    }
    const existing = (state.movements || []).find((entry) => (
        normalizeAuditReference(entry.idempotency_key) === normalizedTransaction.idempotency_key
    ));
    if (existing) {
        if (stableTransactionShape(existing) === stableTransactionShape(normalizedTransaction)) {
            return { ok: true, duplicate: true, state, movement: existing };
        }
        return {
            ok: false,
            state,
            error: { code: "idempotency_conflict", message: "رقم العملية مستخدم سابقًا ببيانات مختلفة." },
        };
    }

    const validationError = validateTransaction(state, normalizedTransaction);
    if (validationError) return { ok: false, state, error: validationError };

    const candidate = {
        ...state,
        movements: [...(state.movements || []), normalizedTransaction],
    };
    const invalidBalance = deriveInventoryBalances(candidate).find((row) => !row.valid);
    if (invalidBalance) {
        return {
            ok: false,
            state,
            error: {
                code: "insufficient_stock",
                message: "الرصيد المتاح لا يكفي لإتمام الحركة.",
                bucket_key: invalidBalance.bucket_key,
            },
        };
    }
    return { ok: true, duplicate: false, state: candidate, movement: normalizedTransaction };
}

export function receivePurchasePreview(state, input) {
    const units = safeUnits(input.quantity_units);
    if (!units) {
        return { ok: false, state, error: { code: "invalid_quantity", message: "كمية الشراء يجب أن تكون عددًا صحيحًا موجبًا." } };
    }
    const unitCostHalalas = input.unit_cost_halalas;
    if (
        unitCostHalalas !== null
        && unitCostHalalas !== undefined
        && (!Number.isSafeInteger(Number(unitCostHalalas)) || Number(unitCostHalalas) < 0)
    ) {
        return { ok: false, state, error: { code: "invalid_unit_cost", message: "تكلفة الوحدة يجب أن تكون عدد هللات صحيحًا غير سالب." } };
    }
    const invoiceReference = normalizeAuditReference(input.invoice_reference);
    const idempotencyKey = `purchase:${invoiceReference}:receipt:1`;
    return postInventoryTransaction(state, {
        id: `movement:${idempotencyKey}`,
        type: "purchase_receipt",
        status: "posted",
        idempotency_key: idempotencyKey,
        occurred_at: input.occurred_at || null,
        reference: { type: "purchase_invoice", id: invoiceReference },
        lines: [{
            role: "receive",
            configuration_id: input.configuration_id,
            location_id: input.location_id,
            condition: "sellable",
            lot_id: `lot-purchase:${invoiceReference}`,
            delta_units: units,
            unit_cost_halalas: unitCostHalalas === null || unitCostHalalas === undefined
                ? null
                : Number(unitCostHalalas),
        }],
    });
}

export function createPersonalizedConfiguration({ productId, sku, color, customerName, attachmentFingerprint }) {
    const normalizedName = normalizeCustomText(customerName);
    const normalizedFingerprint = normalizeAuditReference(attachmentFingerprint);
    const configurationKey = buildInventoryConfigurationKey({
        sku,
        stage: "personalized_ready",
        color,
        customerName: normalizedName,
        attachmentFingerprint: normalizedFingerprint,
    });
    return {
        id: `config:${configurationKey}`,
        product_id: productId,
        sku,
        stage: "personalized_ready",
        option_values: { color },
        custom_values: {
            customer_name: { raw: customerName, normalized: normalizedName },
            ...(normalizedFingerprint ? {
                customer_image: { fingerprint: normalizedFingerprint },
            } : {}),
        },
        configuration_key: configurationKey,
    };
}

export function transformStockPreview(state, input) {
    const units = safeUnits(input.quantity_units);
    if (!units) {
        return { ok: false, state, error: { code: "invalid_quantity", message: "كمية التخصيص يجب أن تكون عددًا صحيحًا موجبًا." } };
    }

    const source = state.configurations.find((entry) => entry.id === input.source_configuration_id);
    const destination = input.destination_configuration;
    if (!source || !destination) {
        return { ok: false, state, error: { code: "unknown_configuration", message: "تكوين المصدر أو الناتج غير معروف." } };
    }
    if (
        source.product_id !== destination.product_id
        || source.option_values?.color !== destination.option_values?.color
        || source.stage !== "base_ready"
        || destination.stage !== "personalized_ready"
    ) {
        return { ok: false, state, error: { code: "invalid_configuration_transition", message: "يجب أن يحتفظ التحويل بنفس المنتج واللون." } };
    }
    if (!destination.custom_values?.customer_name?.normalized) {
        return { ok: false, state, error: { code: "incomplete_configuration", message: "الاسم مطلوب قبل إنشاء مخزون مخصص." } };
    }
    const inputFingerprint = normalizeAuditReference(input.attachment_fingerprint);
    const destinationFingerprint = normalizeAuditReference(
        destination.custom_values?.customer_image?.fingerprint,
    );
    if (input.requires_attachment && (!input.attachment_present || !inputFingerprint || !destinationFingerprint)) {
        return { ok: false, state, error: { code: "incomplete_configuration", message: "الصورة وبصمتها مطلوبتان لمطابقة هذا المنتج المخصص." } };
    }
    if (input.requires_attachment && inputFingerprint !== destinationFingerprint) {
        return { ok: false, state, error: { code: "attachment_mismatch", message: "بصمة الصورة لا تطابق تكوين المنتج الناتج." } };
    }

    const destinationExists = state.configurations.some((entry) => (
        entry.configuration_key === destination.configuration_key
    ));
    const nextState = destinationExists ? state : {
        ...state,
        configurations: [...state.configurations, destination],
    };
    const destinationId = destinationExists
        ? nextState.configurations.find((entry) => entry.configuration_key === destination.configuration_key).id
        : destination.id;

    const productionReference = normalizeAuditReference(input.production_reference);
    const idempotencyKey = `production:${productionReference}`;
    const posted = postInventoryTransaction(nextState, {
        id: `movement:${idempotencyKey}`,
        type: "stock_transform",
        status: "posted",
        idempotency_key: idempotencyKey,
        occurred_at: input.occurred_at || null,
        reference: { type: "production_job", id: productionReference },
        lines: [
            {
                role: "consume",
                configuration_id: source.id,
                location_id: input.source_location_id,
                condition: "sellable",
                lot_id: input.source_lot_id,
                delta_units: -units,
            },
            {
                role: "produce",
                configuration_id: destinationId,
                location_id: input.destination_location_id,
                condition: "sellable",
                lot_id: `lot-production:${productionReference}`,
                delta_units: units,
            },
        ],
        cost_lines: [{ resource_id: "service-engraving", quantity_units: units }],
    });
    return posted.ok ? posted : { ...posted, state };
}

export function receiveApprovedReturnPreview(state, input) {
    const units = safeUnits(input.quantity_units);
    if (!input.approved_for_stock) {
        return { ok: false, state, error: { code: "return_not_approved", message: "يجب مراجعة المرتجع واعتماده قبل إدخاله إلى المخزون." } };
    }
    if (!units) {
        return { ok: false, state, error: { code: "invalid_quantity", message: "كمية المرتجع يجب أن تكون عددًا صحيحًا موجبًا." } };
    }
    const orderReference = normalizeAuditReference(input.order_reference);
    if (!orderReference) {
        return { ok: false, state, error: { code: "missing_order_reference", message: "رقم الطلب الأصلي إلزامي لاعتماد المرتجع." } };
    }
    const configuration = state.configurations.find((entry) => entry.id === input.configuration_id);
    if (!configuration?.custom_values?.customer_name?.normalized) {
        return { ok: false, state, error: { code: "incomplete_configuration", message: "لا يمكن إضافة مرتجع مخصص دون الاسم الكامل." } };
    }
    if (input.requires_attachment && !normalizeAuditReference(
        configuration.custom_values?.customer_image?.fingerprint,
    )) {
        return { ok: false, state, error: { code: "incomplete_configuration", message: "لا يمكن إضافة المرتجع دون بصمة الصورة المطلوبة." } };
    }
    const returnReference = normalizeAuditReference(input.return_reference);
    const idempotencyKey = `return:${returnReference}`;
    return postInventoryTransaction(state, {
        id: `movement:${idempotencyKey}`,
        type: "approved_customer_return",
        status: "posted",
        idempotency_key: idempotencyKey,
        occurred_at: input.occurred_at || null,
        reference: {
            type: "customer_return",
            id: returnReference,
            order_id: orderReference,
        },
        lines: [{
            role: "return_to_stock",
            configuration_id: input.configuration_id,
            location_id: input.location_id,
            condition: "sellable",
            lot_id: `return:${returnReference}`,
            delta_units: units,
        }],
    });
}

export function findReadyStockForOrder(state, {
    productId,
    sku,
    color,
    customerName,
    attachmentFingerprint,
}) {
    const configurationKey = buildInventoryConfigurationKey({
        sku,
        stage: "personalized_ready",
        color,
        customerName,
        attachmentFingerprint,
    });
    const configuration = (state.configurations || []).find((entry) => (
        entry.product_id === productId && entry.configuration_key === configurationKey
    ));
    if (!configuration) {
        return { matched: false, configuration_key: configurationKey, quantity_available: 0, locations: [] };
    }

    const locationMap = new Map((state.locations || []).map((entry) => [entry.id, entry]));
    const rows = deriveInventoryBalances(state).filter((row) => (
        row.configuration_id === configuration.id
        && row.condition === "sellable"
        && row.quantity_available > 0
    ));
    return {
        matched: rows.length > 0,
        configuration_key: configurationKey,
        quantity_available: rows.reduce((sum, row) => sum + row.quantity_available, 0),
        locations: rows.map((row) => ({
            ...row,
            location: locationMap.get(row.location_id) || null,
            location_label: formatStorageLocation(locationMap.get(row.location_id)),
        })),
    };
}
