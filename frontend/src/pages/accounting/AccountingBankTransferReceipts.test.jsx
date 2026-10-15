import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingBankTransferReceipts.jsx"),
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

test("bank transfer review takes bank and amount from the Salla order", () => {
    expect(source).toContain("البنك الذي اختاره العميل");
    expect(source).toContain("مبلغ الطلب");
    expect(source).toContain("البنك والمبلغ يأتيان من طلب سلة");
    expect(source).not.toContain("اختر البنك");
    expect(source).not.toContain("المبلغ الواصل فعلياً");
});

test("reviewer compares receipt with an actual imported bank movement before approval", () => {
    expect(source).toContain("الإيصال المرفوع");
    expect(source).toContain("الحركة التي وصلت فعليًا في البنك");
    expect(source).toContain("اعتماد وصول التحويل");
    expect(source).toContain("اختر الحركة التي وصلت فعليًا في البنك");
    expect(serviceSource).toContain("/bank-candidates");
    expect(serviceSource).toContain("CONFIRM_BANK_TRANSFER_RECEIPT");
});

test("receipt review is available in the native MZ2 movements workspace", () => {
    expect(workspaceSource).toContain('import AccountingBankTransferReceipts from "./AccountingBankTransferReceipts"');
    expect(workspaceSource).toContain("<AccountingBankTransferReceipts accountingPermissions={permissions} />");
    expect(source).not.toContain("/bank-transfer-review");
});

test("no exact incoming movement means no approval button can be submitted", () => {
    expect(source).toContain("ارفع كشف البنك أولًا");
    expect(source).toContain("amount_matches");
    expect(source).toContain("disabled={!selectedMovement");
});
