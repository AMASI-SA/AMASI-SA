
import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingShippingP02.jsx"),
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

test("shipping workspace is native MZ2 and keeps P02 gate visible", () => {
    expect(source).toContain("الشحن والتحصيل — MZ2");
    expect(source).toContain("P02 UAT مفتوح لهذه البيئة");
    expect(source).toContain("P02 مقفل");
    expect(source).toContain("لا ينشئ الشحن بيعًا ثانيًا");
    expect(source).toContain("تكلفة الشحن");
    expect(source).not.toContain("post_delivery_journal");
    expect(source).not.toContain("/store-delivery/drivers/");
});

test("shipping settlement consumes imported bank evidence rather than retyping cash", () => {
    expect(source).toContain("تسوية من حركة البنك/الصندوق");
    expect(source).toContain("المبلغ والبنك والتاريخ والمرجع تأتي من كشف البنك");
    expect(source).toContain("previewAccountingShippingSettlement");
    expect(source).toContain("postAccountingShippingSettlement");
    expect(serviceSource).toContain("/shipping-p02/settlements/preview");
    expect(serviceSource).toContain("/shipping-p02/settlements/post");
});

test("shipping automation covers courier fees and store-driver COD", () => {
    expect(source).toContain("processAccountingStoreDriverPending");
    expect(source).toContain("processAccountingCourierPending");
    expect(serviceSource).toContain("/shipping-p02/store-driver/process-pending");
    expect(serviceSource).toContain("/shipping-p02/courier/process-pending");
    expect(serviceSource).toContain("/shipping-p02/context");
});

test("workspace routes shipping page to native UAT surface", () => {
    expect(workspaceSource).toContain('import AccountingShippingP02 from "./AccountingShippingP02"');
    expect(workspaceSource).toContain('<AccountingShippingP02 accountingPermissions={permissions} />');
});
