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

test("daily accounting is the primary simple surface", () => {
    expect(dailySource).toContain("المحاسبة اليومية");
    expect(dailySource).toContain("إضافة حركة مالية");
    expect(dailySource).toContain("مبلغ وصل إلى البنك");
    expect(dailySource).toContain("رفع ملف تسوية");
    expect(dailySource).toContain("يحتاج منك");
    expect(dailySource).toContain("آخر العمليات");
    expect(dailySource).toContain("التفاصيل والإعدادات المتقدمة");
});

test("dangerous operational controls are no longer rendered globally", () => {
    expect(workspaceSource).not.toContain("<AccountingWriteControl />");
    expect(workspaceSource).not.toContain("<AccountingPeriods />");
    expect(dailySource).toContain("<AccountingWriteControl />");
    expect(dailySource).toContain("<AccountingPeriods />");
    expect(dailySource).toContain("advancedOpen");
});

test("daily write forms reuse accepted P01 endpoints instead of inventing accounting logic", () => {
    expect(dailySource).toContain('api.post(BASE + "/bank-receipts"');
    expect(dailySource).toContain("uploadAccountingSettlementDraft");
    expect(dailySource).toContain("<UnifiedEntryScreen />");
    expect(dailySource).toContain("استيراد كشف البنك الجماعي لم يُربط بعد بمسار MZ2");
});
