import { renderToStaticMarkup } from "react-dom/server";

jest.mock("react-router-dom", () => ({
    Link: ({ to, children }) => <a href={to}>{children}</a>,
}));

jest.mock("../services/mezanSuppliersV2", () => ({
    createMezanSupplier: jest.fn(),
    loadMezanSuppliersWorkspace: jest.fn(() => new Promise(() => {})),
    updateMezanSupplier: jest.fn(),
}));

import MezanSuppliersV2, {
    supplierFormFromRow,
    supplierMatchesQuery,
} from "./MezanSuppliersV2";


test("Mezan 2 supplier page states the independent governed supplier contract", () => {
    const markup = renderToStaticMarkup(<MezanSuppliersV2 />);

    expect(markup).toContain('data-testid="mezan-suppliers-v2-page"');
    expect(markup).toContain("الموردون");
    expect(markup).toContain("لا يتم استيراد أو قراءة أو ربط أي مورد أو رصيد من ميزان القديم");
    expect(markup).toContain("لا تنشأ فواتير أو مديونيات");
    expect(markup).toContain('data-testid="mezan-supplier-add-button"');
});


test("supplier editor model preserves only explicit Mezan 2 service ids", () => {
    expect(supplierFormFromRow({
        company_name: "مورد الحفر",
        service_ids: ["engrave"],
        status: "active",
    })).toMatchObject({
        company_name: "مورد الحفر",
        service_ids: ["engrave"],
        status: "active",
    });
    expect(supplierFormFromRow(null).service_ids).toEqual([]);
});


test("supplier search includes linked service names", () => {
    const supplier = {
        company_name: "مورد أماسي",
        service_links: [{ service_name: "حفر الاسم" }],
    };
    expect(supplierMatchesQuery(supplier, "حفر")).toBe(true);
    expect(supplierMatchesQuery(supplier, "طباعة")).toBe(false);
});
