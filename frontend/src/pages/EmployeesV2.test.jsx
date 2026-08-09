import { renderToStaticMarkup } from "react-dom/server";

let mockSearchParams = new URLSearchParams("");

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, className }) => <a href={to} className={className}>{children}</a>,
    useSearchParams: () => [mockSearchParams, jest.fn()],
}));

jest.mock("./StoreOperationsAccessWorkspace", () => function AccessFixture() {
    return <div data-testid="store-operations-access-fixture">صلاحيات إدارة التجهيز الفعلية</div>;
});

jest.mock("../services/employeesV2", () => ({
    getEmployeesV2: jest.fn(),
    applyEmployeesV2ShadowMigration: jest.fn(),
}));

import EmployeesV2 from "./EmployeesV2";

beforeEach(() => {
    mockSearchParams = new URLSearchParams("");
});

test("employee workspace exposes the guarded salary-preserving migration", () => {
    const markup = renderToStaticMarkup(<EmployeesV2 />);

    expect(markup).toContain("Mezan Employee OS");
    expect(markup).toContain("الموظفون والرواتب");
    expect(markup).toContain("إنشاء النسخة التجريبية");
    expect(markup).toContain("لا تعديل على الرواتب القديمة");
    expect(markup).toContain("لا قيود جديدة أو إعادة احتساب");
    expect(markup).toContain("لا تعديل على السلف والعهد");
    expect(markup).not.toContain("صلاحيات إدارة التجهيز الفعلية");
});

test("permissions stay in the same employee workspace instead of a second page", () => {
    mockSearchParams = new URLSearchParams("workspace=permissions");
    const markup = renderToStaticMarkup(<EmployeesV2 />);

    expect(markup).toContain("صلاحيات إدارة التجهيز الفعلية");
    expect(markup).not.toContain("Mezan Employee OS");
});
