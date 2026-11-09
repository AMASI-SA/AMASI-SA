const fs = require("fs");
const path = require("path");
const source = fs.readFileSync(path.join(__dirname, "components/QoyodUnsentHeaderAlert.jsx"), "utf8");
const definition = source.slice(source.indexOf("export function validSummary"), source.indexOf("const dateLabel"));
const valid = new Function(`${definition.replace("export ", "")} return validSummary;`)();
test("only explicit local mirror evidence is accepted, never queue or provider evidence", () => {
    expect(valid({ count: 0, source: "local_invoice_mirror", updated_at: "2026-11-08T10:00:00Z" })).toBe(true);
    expect(valid({ count: 666, source: "local_invoice_mirror", updated_at: "2026-11-08T10:00:00Z" })).toBe(true);
    expect(valid({ counts: { "لم يُرسل": 271 } })).toBe(false);
    expect(valid({ invoice_presence: { count: 0, complete: true } })).toBe(false);
});
