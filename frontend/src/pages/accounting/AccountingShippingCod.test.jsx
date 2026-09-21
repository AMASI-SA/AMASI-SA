import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingShippingCod.jsx"),
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

test("shipping and COD page is MZ2-native instead of a legacy-link placeholder", () => {
    expect(source).toContain("أسعار شركات الشحن المعتمدة — MZ2");
    expect(source).toContain("تكلفة شركات الشحن للطلبات المسلّمة");
    expect(source).toContain("COD — موصل المتجر");
    expect(source).toContain("تسويات شركات الشحن والمندوبين مع البنك");
    expect(source).not.toContain("/shipping/orders-ledger");
    expect(source).not.toContain("/couriers-ledger");
    expect(source).not.toContain("/bank-transfer-review");
    expect(workspaceSource).toContain('import AccountingShippingCod from "./AccountingShippingCod"');
    expect(workspaceSource).toContain("<AccountingShippingCod");
    expect(workspaceSource).toContain("isOwner={access?.is_owner === true}");
});

test("P02 activation is explicit, owner-gated, and precedes all financial posts", () => {
    expect(serviceSource).toContain("/shipping-p02/activate");
    expect(serviceSource).toContain("ACTIVATE_MZ2_P02");
    expect(source).toContain("P02 — الشحن والتحصيل");
    expect(source).toContain("P02 مقفل");
    expect(source).toContain("تفعيل P02");
    expect(source).not.toContain("ACTIVATE_MZ2_P02");
    expect((source.match(/!p02Active/g) || []).length).toBeGreaterThanOrEqual(3);
});

test("shipping page uses isolated P02 endpoints and review-before-post actions", () => {
    expect(serviceSource).toContain("/shipping-p02/workspace");
    expect(serviceSource).toContain("/shipping-p02/courier-fee/");
    expect(serviceSource).toContain("/shipping-p02/store-driver-cod/");
    expect(serviceSource).toContain("/shipping-p02/settlements/preview");
    expect(serviceSource).toContain("/shipping-p02/settlements/post");
    expect(source).toContain("معاينة");
    expect(source).toContain("اعتماد التكلفة");
    expect(source).toContain("اعتماد COD");
    expect(source).toContain("اعتماد التسوية");
});

test("rate policy distinguishes merchant cost from customer Salla shipping charge", () => {
    expect(source).toContain("السعر هنا هو تكلفة شركة الشحن على المتجر");
    expect(source).toContain("وليس مبلغ الشحن الذي دفعه العميل في سلة");
    expect(source).toContain("gross_expense_no_input_vat");
    expect(source).toContain("كل تعديل ينشئ نسخة مؤرخة");
});

test("bank settlement remains evidence-driven and never asks for debit-credit", () => {
    expect(source).toContain("اختر حركة البنك الفعلية");
    expect(source).toContain("تحويل COD للبنك");
    expect(source).toContain("تسوية صافية بعد خصم أجرة");
    expect(source).toContain("سداد أجرة/ذمة للطرف");
    expect(source).not.toContain("مدين");
    expect(source).not.toContain("دائن");
});
