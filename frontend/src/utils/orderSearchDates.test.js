import { orderSearchDateRange, orderSearchEndDay } from "./orderSearchDates";

test("inclusive final day becomes the following Riyadh midnight", () => {
    expect(orderSearchDateRange({ dateFrom: "2026-10-01", dateTo: "2026-10-17" })).toEqual({
        created_from: "2026-10-01T00:00:00+03:00",
        created_to: "2026-10-18T00:00:00+03:00",
    });
    expect(orderSearchEndDay("2026-10-18T00:00:00+03:00")).toBe("2026-10-17");
});

test("single day and year rollover include the complete selected day", () => {
    const result = orderSearchDateRange({ dateFrom: "2026-12-31", dateTo: "2026-12-31" });
    expect(result.created_to).toBe("2027-01-01T00:00:00+03:00");
    expect(orderSearchEndDay(result.created_to)).toBe("2026-12-31");
    expect(orderSearchEndDay("")).toBe("");
});
