const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), "utf8");
}

const app = read("../../App.js");
const sidebar = read("../../components/Sidebar.jsx");
const accounts = read("../Accounts.jsx");
const adAccounts = read("../AdAccounts.jsx");
const migrationWizard = read("../MigrationWizard.jsx");
const reconciliation = read("../ReconciliationReport.jsx");

test("legacy accounting migration has no navigable or write-capable UI", () => {
    expect(app).toContain(
        '<Route path="/accounting/migration" element={<ProtectedRoute><Navigate to="/integrations-v2?workspace=financial&page=opening-balances" replace /></ProtectedRoute>} />',
    );
    expect(app).not.toContain('import MigrationWizard from "./pages/MigrationWizard"');
    expect(sidebar).not.toContain('data-testid="nav-migration"');
    expect(sidebar).not.toContain('to: "/accounting/migration"');

    expect(migrationWizard).toContain('data-testid="legacy-migration-disabled"');
    expect(migrationWizard).toContain("opening-balances");
    expect(migrationWizard).not.toContain("api.");
    expect(migrationWizard).not.toContain("/accounting/migration/run");

    expect(reconciliation).toContain("للمرجعية فقط");
    expect(reconciliation).toContain("opening-balances");
    expect(reconciliation).not.toContain("api.post");
    expect(reconciliation).not.toContain('data-testid="run-migration-btn"');
    expect(reconciliation).not.toContain("orphan-writeoff-");
});

test("account creation no longer submits legacy opening fields", () => {
    expect(accounts).toContain('data-testid="account-opening-p07-notice"');
    expect(accounts).toContain("opening-balances");
    expect(accounts).not.toContain('data-testid="account-opening-balance-input"');
    expect(accounts).not.toContain('data-testid="account-opening-date-input"');
    expect(accounts).not.toContain("opening_balance: parseFloat(form.opening_balance)");
    expect(accounts).not.toContain("opening_balance_date: form.opening_balance_date");
});

test("ad-account legacy migration and opening writers have no trigger", () => {
    expect(adAccounts).not.toContain("/ad-accounts/migration/apply");
    expect(adAccounts).not.toContain("cleanup-duplicates?dry_run=false");
    expect(adAccounts).not.toContain("/opening`, payload");
    expect(adAccounts).not.toContain('data-testid="adacc-migration-btn"');
    expect(adAccounts).not.toContain("adacc-opening-btn-");
    expect(adAccounts).not.toContain('data-testid="adacc-cleanup-duplicates-btn"');
});
