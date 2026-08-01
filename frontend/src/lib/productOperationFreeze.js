export const PRODUCT_OPERATION_CHOICES_ENABLED = false;
export const FROZEN_FULFILLMENT_TYPE = "requires_preparation";
export const FROZEN_INVENTORY_POLICY = "finished_goods_inventory_not_tracked";

export function applyFrozenProductOperationDefaults(values = {}) {
    if (PRODUCT_OPERATION_CHOICES_ENABLED) return { ...values };
    return {
        ...values,
        fulfillment_type: FROZEN_FULFILLMENT_TYPE,
        inventory_policy: FROZEN_INVENTORY_POLICY,
        stockout_policy: "close_when_out_of_stock",
        low_stock_threshold: 3,
    };
}
