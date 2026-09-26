
import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingBankTransferReviews.jsx"),
    "utf8",
);
const service = fs.readFileSync(
    path.join(__dirname, "../../services/accountingModule.js"),
    "utf8",
);
const workspace = fs.readFileSync(
    path.join(__dirname, "AccountingWorkspace.jsx"),
    "utf8",
);

test("bank transfer review shows receipt amount and bank beside actual bank movement", () => {
    expect(source).toContain("المبلغ الظاهر للمراجعة");
    expect(source).toContain("اسم البنك في طلب سلة");
    expect(source).toContain("إيصال التحويل المرفوع");
    expect(source).toContain("التحويل الذي وصل إلى البنك");
    expect(source).toContain("البنك الفعلي");
    expect(source).toContain("المبلغ الفعلي");
});

test("reviewer explicitly chooses the bank movement and approves once", () => {
    expect(source).toContain("اختر الحركة البنكية الفعلية");
    expect(source).toContain("موافق — الإيصال والتحويل متطابقان");
    expect(source).toContain("لا يختار ميزان التحويل تلقائيًا بالمبلغ فقط");
    expect(source).toContain("approveAccountingBankTransferReview");
    expect(service).toContain("/bank-transfer-reviews/");
    expect(service).toContain("APPROVE_BANK_TRANSFER_RECEIPT");
});

test("bank transfer review is part of the MZ2 financial movements workspace", () => {
    expect(workspace).toContain('import AccountingBankTransferReviews from "./AccountingBankTransferReviews"');
    expect(workspace).toContain('<AccountingBankTransferReviews accountingPermissions={permissions} />');
});
