import { snapchatV2SpendDisplay } from "./snapchatV2SpendDisplay";

test("uses proven provider headline without changing the hourly difference", () => {
    expect(snapchatV2SpendDisplay({
        base_spend_native: 1983.68,
        headline_spend_native: 2152.99,
        unallocated_spend_native: 169.31,
        amount_complete: true,
        hourly_breakdown_complete: false,
    })).toEqual({
        spendNative: 2152.99,
        unallocatedSpendNative: 169.31,
        hourlyBreakdownComplete: false,
    });
});

test("falls back to the existing hourly headline for older responses", () => {
    expect(snapchatV2SpendDisplay({
        base_spend_native: 1983.68,
        amount_complete: true,
    })).toEqual({
        spendNative: 1983.68,
        unallocatedSpendNative: 0,
        hourlyBreakdownComplete: true,
    });
});

test("does not coerce a missing spend into a confirmed zero", () => {
    expect(snapchatV2SpendDisplay({
        base_spend_native: null,
        headline_spend_native: null,
        hourly_breakdown_complete: false,
    })).toEqual({
        spendNative: null,
        unallocatedSpendNative: 0,
        hourlyBreakdownComplete: false,
    });
});
