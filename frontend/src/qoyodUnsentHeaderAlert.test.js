const fs = require("fs");
const path = require("path");
const source = fs.readFileSync(path.join(__dirname, "components/QoyodUnsentHeaderAlert.jsx"), "utf8");
const definition = source.slice(source.indexOf("export function eligibleQoyodUnsentCount"), source.indexOf("export default function"));
const count = new Function(`${definition.replace("export ", "")} return eligibleQoyodUnsentCount;`)();
const now = Date.parse("2026-11-05T12:00:00Z");
const payload = {invoice_presence: {complete: true, count: 3, expires_at: "2026-11-05T12:15:00Z"}};

test("legacy queue count is never presented as provider absence", () => {
    expect(count({counts: {"لم يُرسل": 271}}, now)).toBeNull();
});
test("only fresh complete evidence yields a count, including zero", () => {
    expect(count(payload, now)).toBe(3);
    expect(count({...payload, invoice_presence: {...payload.invoice_presence, count: 0}}, now)).toBe(0);
    expect(count({...payload, scan_truncated: true}, now)).toBeNull();
    expect(count(payload, now + 15 * 60_000)).toBeNull();
    expect(count({...payload, invoice_presence: {...payload.invoice_presence, complete: false}}, now)).toBeNull();
    expect(count({...payload, invoice_presence: {...payload.invoice_presence, count: null}}, now)).toBeNull();
});
test("the link requests the exact verified list and starts at the integration floor", () => {
    expect(source).toContain('status: "verified_absent"');
    expect(source).toContain('const QOYOD_SYNC_START_DATE = "2026-07-01"');
    expect(source).toContain('tab=exceptions&verified_absent=1');
    expect(source).toContain('tab=reconciliation');
});
