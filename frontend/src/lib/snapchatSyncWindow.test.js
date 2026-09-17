import { latestSnapchatSyncRange, snapchatSyncRangeAllowed } from "./snapchatSyncWindow";

const now = Date.parse("2026-09-17T12:00:00Z");

test("last 30 days includes today and 29 prior Riyadh dates", () => {
    expect(latestSnapchatSyncRange(now)).toEqual({ dateFrom: "2026-08-19", dateTo: "2026-09-17" });
    expect(snapchatSyncRangeAllowed(latestSnapchatSyncRange(now), now)).toBe(true);
    expect(snapchatSyncRangeAllowed({ dateFrom: "2026-09-17", dateTo: "2026-09-17" }, now)).toBe(true);
});

test.each([
    ["2026-08-18", "2026-09-17"],
    ["2026-08-01", "2026-09-17"],
    ["2026-08-18", "2026-08-18"],
    ["2026-09-18", "2026-09-18"],
    ["2026-09-17", "2026-09-16"],
    ["2026-02-30", "2026-09-17"],
    ["", "2026-09-17"],
])("rejects synchronization outside the rolling window: %s to %s", (dateFrom, dateTo) => {
    expect(snapchatSyncRangeAllowed({ dateFrom, dateTo }, now)).toBe(false);
});

test("window changes at Riyadh midnight, irrespective of browser timezone", () => {
    expect(latestSnapchatSyncRange(Date.parse("2026-09-16T20:59:00Z"))).toEqual({ dateFrom: "2026-08-18", dateTo: "2026-09-16" });
    expect(latestSnapchatSyncRange(Date.parse("2026-09-16T21:00:00Z"))).toEqual({ dateFrom: "2026-08-19", dateTo: "2026-09-17" });
});
