import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingOpeningBalances.jsx"),
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

test("opening balance UI is MZ2-native and explicit about no legacy migration", () => {
    expect(source).toContain("رصيد افتتاحي جديد لميزان 2");
    expect(source).toContain("لا يقرأ النظام أرصدة ميزان القديم");
    expect(source).toContain("إنشاء معاينة بدون ترحيل");
    expect(source).toContain("اعتماد وترحيل القيد الافتتاحي");
    expect(source).toContain("تفعيل P01");
    expect(source).toContain("لن يفتح P02");
    expect(source).not.toContain("/accounting/migration");
    expect(source).not.toContain("UnifiedEntryScreen");
});

test("opening workflow uses dedicated preview approve and activate endpoints", () => {
    for (const pathName of [
        "/opening-balances",
        "/opening-balances/preview",
        "/opening-balances/approve",
        "/opening-balances/activate",
    ]) {
        expect(serviceSource).toContain(pathName);
    }
    expect(workspaceSource).toContain('import AccountingOpeningBalances from "./AccountingOpeningBalances"');
    expect(workspaceSource).toContain("<AccountingOpeningBalances />");
    expect(workspaceSource).not.toContain("<OpeningBalancesBlocked");
});

test("user supplies evidence and balances while debit credit is derived by backend", () => {
    expect(source).toContain("مراجع الأدلة السبعة");
    expect(source).toContain("الرصيد الطبيعي فقط");
    expect(source).toContain("حقوق الملكية تُحسب تلقائيًا");
    expect(source).not.toContain('name="side"');
});

test("P03 opening identities are selected or fixed instead of free-form", () => {
    expect(source).toContain('const SUPPLIER_CATEGORIES = new Set(["supplier_payable"])');
    expect(source).toContain('inventory_asset: "inventory"');
    expect(source).toContain('input_vat: "input_vat"');
    expect(source).toContain('sales_vat_payable: "sales_vat_payable"');
    expect(source).toContain("اختر المورد من دليل ميزان 2");
    expect(source).toContain('readOnly');
});

