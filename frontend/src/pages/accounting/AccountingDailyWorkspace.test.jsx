import fs from "fs";
import path from "path";

const dailySource = fs.readFileSync(
    path.join(__dirname, "AccountingDailyWorkspace.jsx"),
    "utf8",
);
const workspaceSource = fs.readFileSync(
    path.join(__dirname, "AccountingWorkspace.jsx"),
    "utf8",
);
const serviceSource = fs.readFileSync(
    path.join(__dirname, "../../services/accountingModule.js"),
    "utf8",
);

test("full UAT daily workspace keeps all routine inputs on one simple surface", () => {
    expect(dailySource).toContain("المحاسبة اليومية");
    expect(dailySource).toContain("رفع طلبات سلة");
    expect(dailySource).toContain("رفع كشف البنك");
    expect(dailySource).toContain("رفع ملف تسوية");
    expect(dailySource).toContain("مبلغ واصل يدويًا");
    expect(dailySource).toContain("الرواتب والسلف");
    expect(dailySource).toContain("يحتاج منك");
    expect(dailySource).toContain("آخر العمليات");
});

test("Salla upload automatically scopes recognition to the uploaded file", () => {
    expect(dailySource).toContain("uploadAccountingOrderEvidence");
    expect(dailySource).toContain("recognizeAccountingReadyOrders");
    expect(dailySource).toContain("fileId");
    expect(dailySource).toContain("رفع ومعالجة تلقائيًا");
    expect(serviceSource).toContain("file_id");
});

test("bank statement is enabled through the native MZ2 movement workspace", () => {
    expect(dailySource).toContain("<AccountingDailyMovements");
    expect(dailySource).toContain('activeAction === "bank"');
    expect(dailySource).not.toContain('title="رفع كشف البنك"\n                        detail=' + '"سيقرأ ميزان كشف البنك');
    expect(dailySource).not.toContain('disabled\n                        badge="الخطوة التالية"');
});

test("dangerous controls remain collapsed under advanced owner tools", () => {
    expect(workspaceSource).not.toContain("<AccountingWriteControl />");
    expect(workspaceSource).not.toContain("<AccountingPeriods />");
    expect(dailySource).toContain("<AccountingWriteControl />");
    expect(dailySource).toContain("<AccountingPeriods />");
    expect(dailySource).toContain("advancedOpen");
});

test("simple recent activity still hides technical references", () => {
    expect(dailySource).toContain("dailyFriendlyReference");
    expect(dailySource).toContain("بدون المراجع التقنية");
    expect(dailySource).toContain("daily-accounting-exceptions-limit-note");
});
