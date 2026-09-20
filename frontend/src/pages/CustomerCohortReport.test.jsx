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
    expect(page).toContain("مصدره غير معروف");
    expect(page).toContain("غير متوفر");
    expect(page).toContain("بدل رقم صفر مضلل");
    expect(page).toContain('data-testid="customer-cohort-truncated"');
    expect(page).not.toContain("customer_name");
    expect(page).not.toContain("customer_mobile");
    expect(page).not.toContain("customer_email");
});

test("explains the report in plain Arabic without overlapping-window ambiguity", () => {
    expect(page).toContain("الخلاصة ببساطة");
    expect(page).toContain("أرقام آخر 30 يومًا جزء من أرقام آخر 60 يومًا");
    expect(page).toContain("يشمل آخر 30 يومًا");
    expect(page).toContain("الجدول يعرض من بدأوا الشراء خلال آخر 60 يومًا فقط");
    expect(page).toContain("مصدر أول طلب غير معروف لجميع العملاء");
    expect(page).not.toContain('label="عائدون خلال 30 يومًا"');
    expect(page).not.toContain("اكتمال {value.coverage_pct}%");
});
