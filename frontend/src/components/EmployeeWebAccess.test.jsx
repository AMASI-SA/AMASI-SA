import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import api from "../lib/api";
import { useAuth, useOptionalAuth } from "../context/AuthContext";
import EmployeesV2 from "../pages/EmployeesV2";
import WebPagePermissions from "../pages/WebPagePermissions";
import EmployeeWebLayout from "./EmployeeWebLayout";
import OwnerOnlyRoute from "./OwnerOnlyRoute";
import ProtectedRoute from "./ProtectedRoute";

jest.mock("react-router/dom", () => {
    const { TextEncoder, TextDecoder } = require("util");
    if (!global.TextEncoder) global.TextEncoder = TextEncoder;
    if (!global.TextDecoder) global.TextDecoder = TextDecoder;
    const path = require("path");
    const packagePath = require.resolve("react-router/package.json");
    const target = require(packagePath).exports["./dom"].node.default;
    return require(path.resolve(path.dirname(packagePath), target));
}, { virtual: true });
jest.mock("../context/AuthContext", () => ({ useAuth: jest.fn(), useOptionalAuth: jest.fn() }));
jest.mock("../pages/AmasiDeliveryApp", () => () => null);
jest.mock("../lib/api", () => ({ get: jest.fn(), put: jest.fn() }));

const PRODUCTS = { id: "products.catalog", section: "products", label: "إدارة المنتجات", to: "/products-v2", write_label: "مسودة تعديل" };
const ORDERS = { id: "orders.list", section: "orders", label: "كل الطلبات", to: "/orders-v2" };
const REPORTS = {
    id: "accounting.journals_reports.view",
    section: "accounting",
    label: "القيود والتقارير",
    to: "/integrations-v2?workspace=financial&page=journals-reports",
};

let host;
let root;

beforeEach(() => {
    useOptionalAuth.mockReturnValue({});
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});

afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

function byTestId(id) {
    return host.querySelector('[data-testid="' + id + '"]');
}

async function renderEmployee(url, pages) {
    useAuth.mockReturnValue({
        loading: false,
        user: { id: "employee", role: "user", web_page_access: { pages } },
    });
    await act(async () => root.render(
        <MemoryRouter initialEntries={[url]}>
            <Routes>
                <Route path="/" element={<ProtectedRoute><h1>الرئيسية</h1></ProtectedRoute>} />
                <Route path="/products-v2" element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute employeePages={[PRODUCTS.id]}>
                            <EmployeeWebLayout pages={pages}><h1>كتالوج المتجر</h1></EmployeeWebLayout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                } />
                <Route path="/integrations-v2" element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <EmployeeWebLayout pages={pages}><h1>تقارير المتجر</h1></EmployeeWebLayout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                } />
            </Routes>
        </MemoryRouter>
    ));
}

test("a products-only employee lands on the catalogue and can expand the only granted section", async () => {
    await renderEmployee("/", [PRODUCTS]);
    expect(host.querySelector("h1").textContent).toBe("كتالوج المتجر");
    expect(byTestId("mezan-v2-primary-orders")).toBeNull();
    expect(byTestId("mezan-v2-open-all")).toBeNull();
    expect(byTestId("mezan-v2-secondary-products")).toBeNull();

    const productsSection = byTestId("mezan-v2-primary-products");
    expect(productsSection.tagName).toBe("BUTTON");
    await act(async () => productsSection.click());
    const links = Array.from(byTestId("mezan-v2-secondary-products").querySelectorAll("a"));
    expect(links.map((link) => [link.textContent, link.getAttribute("href")]))
        .toEqual([["إدارة المنتجات", "/products-v2"]]);
});

test("a granted product detail can be opened by direct link", async () => {
    await renderEmployee("/products-v2?product=123", [PRODUCTS]);
    expect(host.querySelector("h1").textContent).toBe("كتالوج المتجر");
});

test.each([
    "/products-v2?workspace=access",
    "/products-v2?workspace=intake",
    REPORTS.to,
])("an ungranted direct link %s leaves the allowed header available", async (url) => {
    await renderEmployee(url, [PRODUCTS]);
    expect(host.querySelector("h1").textContent).toBe("هذه الصفحة غير متاحة لحسابك");
    expect(byTestId("mezan-v2-primary-products")).not.toBeNull();
    expect(byTestId("mezan-v2-primary-accounting")).toBeNull();
});

test("the accounting header lists only the accounting page granted along with products", async () => {
    await renderEmployee(REPORTS.to, [PRODUCTS, REPORTS]);
    expect(host.querySelector("h1").textContent).toBe("تقارير المتجر");
    expect(byTestId("mezan-v2-primary-products")).not.toBeNull();
    await act(async () => byTestId("mezan-v2-primary-accounting").click());
    const links = Array.from(byTestId("mezan-v2-secondary-accounting").querySelectorAll("a"));
    expect(links.map((link) => link.getAttribute("href"))).toEqual([REPORTS.to]);
});

test("the owner can save a selection spanning sections in one request", async () => {
    const member = { id: "employee", name: "موظف اختبار", revision: 4, page_modes: {} };
    const inventory = { id: "products.inventory", section: "products", label: "المخزون", to: "/inventory-v2" };
    api.get.mockResolvedValue({ data: { members: [member], pages: [PRODUCTS, inventory, ORDERS, REPORTS] } });
    const pageModes = { [PRODUCTS.id]: "write", [inventory.id]: "read", [REPORTS.id]: "read" };
    api.put.mockResolvedValue({ data: { member: { ...member, revision: 5, page_modes: pageModes } } });
    await act(async () => root.render(<WebPagePermissions initialMemberId={member.id} />));

    const sectionSelect = host.querySelectorAll("select")[1];
    await act(async () => Array.from(host.querySelectorAll("button"))
        .find((button) => button.textContent === "قراءة وكتابة للجميع").click());
    await act(async () => {
        sectionSelect.value = "accounting";
        sectionSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
        const reports = host.querySelector('select[aria-label="القيود والتقارير"]');
        reports.value = "read";
        reports.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => host.querySelector('button[type="submit"]').click());

    expect(api.put).toHaveBeenCalledWith("/web-page-access-v1/employee", {
        expected_revision: 4,
        page_modes: pageModes,
    });
    expect(host.querySelector('[role="status"]').textContent).toBe("تم حفظ الصفحات.");
});

test("new global permission groups retain grants in other sections and link to the selected financial identity", async () => {
    const member = { id: "employee/a", name: "موظف اختبار", revision: 2, page_modes: {[ORDERS.id]: "read"} };
    const bank = { id:"balances.assets", to:"/operational-balances?view=assets", label:"الأصول والحسابات", section:"balances", section_label:"الأرصدة التشغيلية" };
    const admin = { id:"balances.access", to:"/operational-balances/access", label:"إدارة حسابات الموظفين", section:"balances" };
    api.get.mockResolvedValue({ data: { members: [member], pages: [ORDERS,bank,admin] } });
    api.put.mockResolvedValue({ data:{member:{...member,page_modes:{[ORDERS.id]:"read",[bank.id]:"read",[admin.id]:"read"}}} });
    await act(async () => root.render(<WebPagePermissions initialMemberId={member.id}/>));
    const group=host.querySelectorAll("select")[1];
    await act(async () => {group.value="balances";group.dispatchEvent(new Event("change",{bubbles:true}));});
    expect(host.querySelector('a[href="/operational-balances/access?member=employee%2Fa"]')).toBeTruthy();
    expect(host.querySelector('select[aria-label="إدارة حسابات الموظفين"]').disabled).toBe(false);
    await act(async () => [...host.querySelectorAll("button")].find(b=>b.textContent==='قراءة للجميع').click());
    await act(async () => host.querySelector('button[type="submit"]').click());
    expect(api.put).toHaveBeenCalledWith("/web-page-access-v1/employee%2Fa", {expected_revision:2,page_modes:{[ORDERS.id]:"read",[bank.id]:"read",[admin.id]:"read"}});
});

test("Arabic search works across departments and bulk changes only its visible assignable rows", async () => {
    const member = { id:"staff", name:"موظف", revision:1, page_modes:{[ORDERS.id]:"read"} };
    const statement = {id:"balances.statements",section:"balances",section_label:"الأرصدة التشغيلية",label:"كشوف المنصات",to:"/operational-balances/provider-statements",write_label:"رفع الكشف"};
    const payroll = {id:"employees.payroll",section:"employees",section_label:"الموظفون",label:"كشوف الرواتب",to:"/employees-v2?workspace=migration"};
    const personalReport = {...payroll,id:"employees.private",label:"كشوف خاصة"};
    api.get.mockClear().mockResolvedValue({data:{members:[member],pages:[ORDERS,statement,payroll,personalReport]}});
    api.put.mockResolvedValue({data:{member}});
    await act(async()=>root.render(<WebPagePermissions initialMemberId={member.id}/>));
    const input=host.querySelector('input[type=search]');
    await act(async()=>{
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,"كُشُوف");
        input.dispatchEvent(new Event("input",{bubbles:true}));
    });
    expect(host.querySelector('select[aria-label="كل الطلبات"]')).toBeNull();
    expect(host.querySelector('select[aria-label="كشوف المنصات"]')).toBeTruthy();
    expect(host.querySelector('select[aria-label="كشوف الرواتب"]')).toBeTruthy();
    await act(async()=>[...host.querySelectorAll("button")].find(x=>x.textContent==="قراءة وكتابة للجميع").click());
    await act(async()=>host.querySelector('button[type=submit]').click());
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.put).toHaveBeenCalledWith("/web-page-access-v1/staff",{expected_revision:1,page_modes:{
        [ORDERS.id]:"read",[statement.id]:"write",[payroll.id]:"read",[personalReport.id]:"read"
    }});
});

test("the owner grants the entire catalogue in one save, with write limited to supported operations", async () => {
    const self = {id:"employees.permissions",section:"employees",label:"إدارة الصلاحيات",to:"/employees-v2?workspace=permissions"};
    const member = {id:"staff",name:"موظف",revision:3,page_modes:{}};
    api.get.mockResolvedValue({data:{members:[member],pages:[PRODUCTS, ORDERS, self]}});
    api.put.mockClear().mockResolvedValue({data:{member}});
    await act(async () => root.render(<WebPagePermissions initialMemberId="staff"/>));
    const group = host.querySelectorAll("select")[1];
    await act(async () => {group.value="*";group.dispatchEvent(new Event("change",{bubbles:true}));});
    const button = label => [...host.querySelectorAll("button")].find(row => row.textContent === label);
    await act(async () => button("قراءة للجميع").click());
    await act(async () => button("إزالة جميع الصفحات").click());
    expect(host.querySelector('select[aria-label="إدارة الصلاحيات"]').value).toBe("");
    await act(async () => button("قراءة وكتابة للجميع").click());
    expect(api.put).not.toHaveBeenCalled();
    await act(async () => host.querySelector('button[type=submit]').click());
    expect(api.put).toHaveBeenCalledWith("/web-page-access-v1/staff",{expected_revision:3,page_modes:{
        [PRODUCTS.id]:"write",[ORDERS.id]:"read",[self.id]:"read",
    }});
});

test.each(["", "&panel=roles&member=other", "&panel=accounting-actions&member=other"])("a grant to permissions shows only the authenticated employee's own pages: %s", async extra => {
    const self = {id:"employees.permissions",section:"employees",section_label:"الموظفون",label:"إدارة الصلاحيات",to:"/employees-v2?workspace=permissions",mode:"read"};
    const state = { user:{id:"staff",name:"سارة",role:"employee",web_page_access:{pages:[self,{...PRODUCTS,mode:"write"}]}},loading:false };
    useAuth.mockReturnValue(state);
    useOptionalAuth.mockReturnValue(state);
    api.get.mockClear(); api.put.mockClear();
    await act(async () => root.render(<MemoryRouter initialEntries={[self.to+extra]}>
        <ProtectedRoute><OwnerOnlyRoute><EmployeesV2/></OwnerOnlyRoute></ProtectedRoute>
    </MemoryRouter>));
    expect(host.querySelector("h1").textContent).toBe("صلاحيات حسابي");
    expect(host.textContent).toContain("سارة");
    expect(host.textContent).toContain(PRODUCTS.write_label);
    expect([...host.querySelectorAll("a")].map(link=>link.getAttribute("href"))).toEqual([self.to,PRODUCTS.to]);
    expect(host.querySelector('form,select,input')).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
});
