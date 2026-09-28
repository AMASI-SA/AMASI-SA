
import fs from "fs";
import path from "path";

const dailySource = fs.readFileSync(
    path.join(__dirname, "AccountingDailyWorkspace.jsx"),
    "utf8",
);
const actionsSource = fs.readFileSync(
    path.join(__dirname, "AccountingDailyAutomationActions.jsx"),
    "utf8",
);
const statusSource = fs.readFileSync(
    path.join(__dirname, "AccountingDailyAutomationStatus.jsx"),
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

test("daily accounting is the one-screen automation-first home", () => {
    expect(dailySource).toContain("المحاسبة اليومية");
    expect(dailySource).toContain("AccountingDailyAutomationActions");
    expect(dailySource).toContain("AccountingDailyAutomationStatus");
    expect(actionsSource).toContain("رفع طلبات سلة");
    expect(actionsSource).toContain("رفع كشف البنك");
    expect(actionsSource).toContain("رفع ملف تسوية");
    expect(actionsSource).toContain("إضافة مبلغ واصل");
    expect(actionsSource).toContain("تلقائي");
});

test("daily upload actions use only native MZ2 accounting paths", () => {
    expect(actionsSource).toContain("uploadAccountingOrderEvidence");
    expect(actionsSource).toContain("recognizeAccountingReadyOrders");
    expect(actionsSource).toContain("processAccountingStoreDriverPending");
    expect(actionsSource).toContain("uploadAccountingDailyMovements");
    expect(actionsSource).toContain("uploadAccountingSettlementDraft");
    expect(actionsSource).not.toContain("UnifiedEntryScreen");
    expect(actionsSource).not.toContain("/financial-movements");
    expect(serviceSource).toContain("/order-evidence/upload");
    expect(serviceSource).toContain("/order-recognition/recognize-ready");
    expect(serviceSource).toContain("/daily-movements/upload");
    expect(serviceSource).toContain("/shipping-p02/store-driver/process-pending");
});

test("exceptions separate actions from automatic waiting and recent rows hide technical references", () => {
    expect(statusSource).toContain("يحتاج منك");
    expect(statusSource).toContain("لا يحتاج قرارًا الآن");
    expect(statusSource).toContain("للعلم");
    expect(statusSource).toContain("القائمة مختصرة إلى");
    expect(statusSource).toContain("بدون المراجع التقنية");
    expect(statusSource).toContain("friendlyReference");
    expect(statusSource).toContain("text.length > 28");
    expect(statusSource).toContain("text.includes");
});

test("dangerous accounting controls are no longer global", () => {
    expect(workspaceSource).not.toContain("<AccountingWriteControl />");
    expect(workspaceSource).not.toContain("<AccountingPeriods />");
    expect(dailySource).toContain("<AccountingWriteControl />");
    expect(dailySource).toContain("<AccountingPeriods />");
    expect(dailySource).toContain("advancedOpen");
});

test("home replaces engineering header while detailed pages retain it", () => {
    expect(workspaceSource).toContain('page.id !== "home"');
    expect(workspaceSource).toContain("<AccountingHeader");
    expect(workspaceSource).toContain("<AccountingDailyWorkspace");
});
