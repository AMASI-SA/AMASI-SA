import fs from "fs";
import path from "path";

const registerSource = fs.readFileSync(
    path.join(__dirname, "AccountingSettlementRegister.jsx"),
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

test("P01 workspace mounts a searchable settlement register under settlements", () => {
    expect(workspaceSource).toContain('import AccountingSettlementRegister from "./AccountingSettlementRegister"');
    expect(workspaceSource).toContain("<AccountingSettlementRegister accountingPermissions={permissions} />");
    expect(registerSource).toContain('data-testid="accounting-settlement-register"');
    expect(registerSource).toContain('data-testid="settlement-register-filter-form"');
    expect(registerSource).toContain('data-testid="settlement-register-list"');
    expect(registerSource).toContain('data-testid="settlement-register-detail"');
});

test("register displays authoritative matched and reversed lifecycle states in Arabic", () => {
    expect(registerSource).toContain('matched: ["تمت المطابقة"');
    expect(registerSource).toContain('reversed: ["معكوسة"');
    expect(registerSource).toContain("عملة التسوية غير محفوظة أو غير مدعومة");
});

test("register exposes bank evidence matching without posting a second journal", () => {
    expect(registerSource).toContain("getAccountingSettlementBankCandidates");
    expect(registerSource).toContain("saveAccountingSettlementBankMatch");
    expect(registerSource).toContain('data-testid="settlement-bank-candidates"');
    expect(registerSource).toContain('data-testid="save-settlement-bank-match"');
    expect(registerSource).toContain("لا ينشئ قيدًا ثانيًا ولا يغير الرصيد");
});

test("frontend service exposes register, detail, and bank-match endpoints", () => {
    expect(serviceSource).toContain("getAccountingSettlementRegister");
    expect(serviceSource).toContain("getAccountingSettlementRegisterDetail");
    expect(serviceSource).toContain("getAccountingSettlementBankCandidates");
    expect(serviceSource).toContain("saveAccountingSettlementBankMatch");
    expect(serviceSource).toContain("/bank-candidates");
    expect(serviceSource).toContain("/bank-match");
    expect(serviceSource).toContain("`${SETTLEMENTS}/register`");
});

test("legacy settlements screen receives matched as ready_for_review without losing workflow state", () => {
    expect(serviceSource).toContain("normalizeSettlementDraftForUi");
    expect(serviceSource).toContain('status: "ready_for_review"');
    expect(serviceSource).toContain('workflow_state: draft.workflow_state || "matched"');
});
