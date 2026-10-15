import {
    FROZEN_FULFILLMENT_TYPE,
    FROZEN_INVENTORY_POLICY,
    PRODUCT_OPERATION_CHOICES_ENABLED,
    applyFrozenProductOperationDefaults,
} from "./productOperationFreeze";

test("freezes every product to preparation and ignores dormant choices", () => {
    expect(PRODUCT_OPERATION_CHOICES_ENABLED).toBe(false);
    expect(FROZEN_FULFILLMENT_TYPE).toBe("requires_preparation");
    expect(FROZEN_INVENTORY_POLICY).toBe(
        "finished_goods_inventory_not_tracked",
    );
    expect(applyFrozenProductOperationDefaults({
        fulfillment_type: "instant",
        inventory_policy: "branch_stock_required",
        stockout_policy: "allow_preorder",
        low_stock_threshold: 99,
    })).toEqual({
        fulfillment_type: "requires_preparation",
        inventory_policy: "finished_goods_inventory_not_tracked",
        stockout_policy: "close_when_out_of_stock",
        low_stock_threshold: 3,
    });
});
