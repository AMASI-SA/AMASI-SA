const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

test("the store header warns about eligible Qoyod orders that were not sent", () => {
    const layout = read("src/components/Layout.jsx");
    const alert = read("src/components/QoyodUnsentHeaderAlert.jsx");

    expect(layout).toContain('import QoyodUnsentHeaderAlert from "./QoyodUnsentHeaderAlert";');
    expect(layout).toContain("<QoyodUnsentHeaderAlert />");

    expect(alert).toContain('payload?.counts?.["لم يُرسل"]');
    expect(alert).toContain('const QOYOD_SYNC_START_DATE = "2026-07-01";');
    expect(alert).toContain("from_date: QOYOD_SYNC_START_DATE");
    expect(alert).not.toContain("days: 365");
    expect(alert).toContain("limit: 5000");
    expect(alert).toContain('status: "لم يُرسل"');
    expect(alert).toContain("طلب مؤهل منذ 1 يوليو 2026 لم يُرسل إلى قيود");
    expect(alert).toContain("15_000");
    expect(alert).toContain('data-testid="qoyod-unsent-header-alert-count"');
    expect(alert).toContain('to="/integrations-v2/qoyod?tab=exceptions"');
});

test("the warning stays hidden at zero and does not count sent, duplicate, or refunded invoices", () => {
    const alert = read("src/components/QoyodUnsentHeaderAlert.jsx");
    const source = read("../backend/integrations/qoyod/unsent_orders.py");

    expect(alert).toContain("if (count === 0) return null;");
    expect(source).toContain("if in_qoyod_by_reference:");
    expect(source).toContain('return {"status": SENT,');
    expect(source).toContain("counts[e[\"status\"]] += 1");
    expect(source).toContain("if order_date is None:");
    expect(source).toContain("if order_date < sync_start:");
    expect(source).not.toContain('return received.date()');
});
