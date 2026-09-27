import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingPayroll.jsx"),
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

test("payroll workspace is MZ2-native and bank-evidence driven", () => {
    expect(source).toContain("الرواتب والسلف والعهد — ميزان 2");
    expect(source).toContain("لا يُكتب يدويًا من جديد");
    expect(source).toContain("حركات البنك التي تنتظر تصنيف موظف");
    expect(source).toContain("خصم السلفة المفتوحة");
    expect(source).not.toContain("/accounting/employees/");
    expect(source).not.toContain("UnifiedEntryScreen");
});

test("payroll UI exposes accrual and movement classification through accounting module only", () => {
    expect(serviceSource).toContain("/payroll/context");
    expect(serviceSource).toContain("/payroll/accrue");
    expect(serviceSource).toContain("/payroll/movements/");
    expect(workspaceSource).toContain('import AccountingPayroll from "./AccountingPayroll"');
    expect(workspaceSource).toContain("<AccountingPayroll accountingPermissions={permissions} />");
});

test("employee cash amount and date come from imported bank movement", () => {
    expect(source).toContain("مبلغ البنك وتاريخه لا يمكن تغييره من هنا");
    expect(source).toContain("صرف راتب");
    expect(source).toContain("منح سلفة");
    expect(source).toContain("تسليم عهدة");
    expect(source).toContain("استرداد سلفة");
    expect(source).toContain("إرجاع عهدة");
});
