import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingDailyMovements.jsx"),
    "utf8",
);
const serviceSource = fs.readFileSync(
    path.join(__dirname, "../../services/accountingModule.js"),
    "utf8",
);
const workspaceSource = fs.readFileSync(
    path.join(__dirname, "AccountingWorkspace.jsx"),
    "utf8",
);

test("daily movement workspace is evidence-first and MZ2-native", () => {
    expect(source).toContain("كشف البنك والحركات اليومية — MZ2");
    expect(source).toContain("رفع الكشف لا ينشئ قيدًا");
    expect(source).toContain("أي صف غير مصنف يبقى دليلًا فقط");
    expect(source).toContain("تأكيد المزود");
    expect(source).not.toContain("/financial-movements");
    expect(source).not.toContain("UnifiedEntryScreen");
});

test("daily movement UI uses dedicated accounting module endpoints", () => {
    expect(serviceSource).toContain("/daily-movements/context");
    expect(serviceSource).toContain("/daily-movements/upload");
    expect(serviceSource).toContain("/confirm-provider");
    expect(workspaceSource).toContain('import AccountingDailyMovements from "./AccountingDailyMovements"');
    expect(workspaceSource).toContain("<AccountingDailyMovements accountingPermissions={permissions} />");
});

test("provider inference is presented as a review suggestion, not automatic identity", () => {
    expect(source).toContain("يعرضه ميزان كاقتراح ولا يعتمد عليه تلقائيًا");
    expect(source).toContain("عمود «المنصة» الصريح");
});


test("accountant can add a manual incoming bank transfer without debit-credit fields", () => {
    expect(source).toContain("إضافة تحويل بنكي يدوي");
    expect(source).toContain("اسم المحوّل");
    expect(source).toContain("مرجع التحويل");
    expect(source).toContain("createAccountingManualIncomingMovement");
    expect(serviceSource).toContain("/daily-movements/manual-incoming");
    expect(source).toContain("لا تختار مدين/دائن");
});

test("accountant can enter and classify outgoing bank movements without debit-credit fields", () => {
    expect(source).toContain("إضافة حركة بنكية يدوية");
    expect(source).toContain("وارد إلى البنك");
    expect(source).toContain("خارج من البنك");
    expect(source).toContain("createAccountingManualOutgoingMovement");
    expect(serviceSource).toContain("/daily-movements/manual-outgoing");
    expect(source).toContain("مصروف عام");
    expect(source).toContain("سداد مورد قائم");
    expect(source).toContain("classifyAccountingOutgoingMovement");
    expect(serviceSource).toContain("/classify-outgoing");
    expect(source).toContain("لا تختار مدين/دائن");
    expect(source).toContain("إنشاء المشتريات والمخزون يبقى ضمن P03");
});

