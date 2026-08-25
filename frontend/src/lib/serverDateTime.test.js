import {
  isRecentServerDateTime,
  parseServerDateTime,
} from "./serverDateTime";


test("parses naive backend timestamps as UTC", () => {
  expect(parseServerDateTime("2026-08-25 00:11:03")).toBe(
    Date.parse("2026-08-25T00:11:03Z"),
  );
  expect(parseServerDateTime("2026-08-25T00:11:03")).toBe(
    Date.parse("2026-08-25T00:11:03Z"),
  );
});


test("preserves explicit server time zones", () => {
  expect(parseServerDateTime("2026-08-25T03:11:03+03:00")).toBe(
    Date.parse("2026-08-25T03:11:03+03:00"),
  );
});


test("recognizes recent naive UTC timestamps", () => {
  const nowMs = Date.parse("2026-08-25T00:21:03Z");

  expect(
    isRecentServerDateTime("2026-08-25 00:11:03", nowMs),
  ).toBe(true);
  expect(
    isRecentServerDateTime("2026-08-24 23:50:02", nowMs),
  ).toBe(false);
});


test("rejects invalid timestamps", () => {
  expect(Number.isNaN(parseServerDateTime(""))).toBe(true);
  expect(Number.isNaN(parseServerDateTime(null))).toBe(true);
  expect(Number.isNaN(parseServerDateTime("not-a-date"))).toBe(true);
});
