// A product option belongs to its product; delimiters inside IDs cannot collide.
export const inventoryIdentity = line => JSON.stringify([line.kind, line.item_id ?? line.id, line.variant_id ?? null]);

export const inventoryVariant = line => line.variant_id == null ? {} : {variant_id: line.variant_id};
