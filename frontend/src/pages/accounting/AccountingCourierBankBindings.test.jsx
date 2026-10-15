import fs from "fs";
import path from "path";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingCourierBankBindings.jsx"),
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

test("P01 renders external courier bank bindings under the settlements page", () => {
    expect(workspaceSource).toContain(
        'import AccountingCourierBankBindings from "./AccountingCourierBankBindings"',
    );
    expect(workspaceSource).toContain(
        "<AccountingCourierBankBindings accountingPermissions={permissions} />",
    );
    expect(source).toContain('data-testid="accounting-courier-bank-bindings"');
});

test("courier P01 UI is bank-only and keeps driver and COD logic locked", () => {
    expect(source).toContain("بنوك تسوية شركات الشحن الخارجية");
    expect(source).toContain("المندوبون الداخليون والاستلام المباشر مستبعدون");
    expect(source).toContain("تكلفة الشحن، شرائح COD، أرصدة الشركات ومناديب المتجر تبقى مقفلة إلى P02");
    expect(source).not.toContain("cost_per_order");
    expect(source).not.toContain("cod_fee_percent");
    expect(source).not.toContain("cod_fee_tiers");
});

test("courier binding requires independent rule-management permission", () => {
    expect(source).toContain('accountingPermissions.includes("accounting.rules.manage")');
    expect(source).toContain("confirmed: true");
    expect(source).toContain("لا يسجل تكلفة أو رصيد COD");
});

test("frontend client exposes courier binding read and write endpoints", () => {
    expect(serviceSource).toContain("getAccountingCourierBankBindings");
    expect(serviceSource).toContain("saveAccountingCourierBankBinding");
    expect(serviceSource).toContain("/courier-bindings");
});
