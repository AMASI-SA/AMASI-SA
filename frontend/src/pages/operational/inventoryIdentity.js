// A product option belongs to its product; delimiters inside IDs cannot collide.
export const inventoryIdentity = line => JSON.stringify([line.kind, line.item_id ?? line.id, line.variant_id ?? null, ...(line.purchase_line_key ? [line.purchase_line_key] : [])]);

export const inventoryVariant = line => ({...(line.variant_id == null ? {} : {variant_id: line.variant_id}),...(line.purchase_line_key ? {purchase_line_key:line.purchase_line_key} : {})});
