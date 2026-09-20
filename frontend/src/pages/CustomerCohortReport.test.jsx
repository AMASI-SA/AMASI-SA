import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "CustomerCohortReport.jsx"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
const navigation = fs.readFileSync(
    path.join(__dirname, "../components/MezanV2NavigationShellLegacy.jsx"),
    "utf8",
);

test("registers the owner-only customer cohort report in marketing navigation", () => {
    expect(navigation).toContain('{ to: "/ads-manager/customer-cohorts", label: "أفواج العملاء", exactSearch: true }');
    expect(app).toContain('path="/ads-manager/customer-cohorts"');
    expect(app).toContain("<OwnerOnlyRoute>");
    expect(app).toContain("<Layout><CustomerCohortReport /></Layout>");
});

test("keeps the page aggregate-only and visibly fail-closed for partial data", () => {
    expect(page).toContain('data-testid="customer-cohort-report-page"');
    expect(page).toContain("قراءة فقط");
    expect(page).toContain("غير محسوم");
    expect(page).toContain("المبلغ الناقص يبقى غير معلوم");
    expect(page).toContain('data-testid="customer-cohort-truncated"');
    expect(page).not.toContain("customer_name");
    expect(page).not.toContain("customer_mobile");
    expect(page).not.toContain("customer_email");
});
