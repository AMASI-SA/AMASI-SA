import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingInventoryPurchases.jsx"),
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

test("P03 page is native and wired into the accounting workspace", () => {
    expect(source).toContain("P03 — المخزون والمشتريات");
    expect(source).toContain("فاتورة شراء للمخزون");
    expect(source).toContain("استلامات المخزون الفعلية الجاهزة للمحاسبة");
    expect(workspaceSource).toContain('import AccountingInventoryPurchases from "./AccountingInventoryPurchases"');
    expect(workspaceSource).toContain("<AccountingInventoryPurchases");
});

test("P03 requires explicit phase activation and accountant posting", () => {
    expect(serviceSource).toContain("/inventory-p03/activate");
    expect(source).not.toContain("ACTIVATE_MZ2_P03");
    expect(source).toContain("P03 مقفل");
    expect(source).toContain("تفعيل P03");
    expect(source).toContain('accounting.purchases.post');
});

test("purchase invoice is receipt-driven and never uses the legacy liability API", () => {
    expect(serviceSource).toContain("/inventory-p03/purchase-invoices");
    expect(serviceSource).toContain("/inventory-p03/inventory-receipts/");
    expect(source).toContain("الذمة وتكلفة المخزون تُثبت فقط عند استلام الكمية فعليًا");
    expect(source).not.toContain("/purchase-invoices");
    expect(source).not.toContain("/liabilities");
    expect(source).not.toContain("مدين");
    expect(source).not.toContain("دائن");
});

test("P03 UI exposes explicit purchase tax treatment", () => {
    expect(source).toContain("ضريبة مدخلات قابلة للاسترداد");
    expect(source).toContain("تضاف إلى تكلفة المخزون");
    expect(source).toContain("recoverable_input_vat");
    expect(source).toContain("included_in_inventory_cost");
    expect(source).toContain("مرجع الدليل الضريبي");
    expect(source).toContain("رقم/مرجع الفاتورة الضريبية");
    expect(source).toContain("أدخل مرجع الفاتورة أو الدليل الضريبي");
});

test("COGS waits for sale recognition and uses receipt-lot cost evidence", () => {
    expect(serviceSource).toContain("/inventory-p03/inventory-consumptions/");
    expect(serviceSource).toContain("/cogs-preview");
    expect(serviceSource).toContain("/cogs-post");
    expect(source).toContain("تكلفة البضاعة المباعة — COGS");
    expect(source).toContain("لا يُرحّل COGS حتى يظهر قيد بيع MZ2 لنفس الطلب");
    expect(source).toContain("تكلفة الاستلام الأصلية");
    expect(source).toContain("لا تُستخدم تكلفة الكتالوج الحالية");
    expect(source).toContain("معاينة COGS");
    expect(source).toContain("اعتماد COGS");
});

test("Inventory V2 remains the operational receiving source", () => {
    expect(source).toContain('to="/inventory-receiving-v2"');
    expect(source).toContain("Inventory V2");
    expect(source).toContain("معاينة");
    expect(source).toContain("اعتماد الاستلام");
});
