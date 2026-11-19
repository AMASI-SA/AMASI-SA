import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { grantedPageAt } from "../../lib/webPageAccess";
import EmployeeBalanceSpecialist from "./EmployeeBalanceSpecialist";
import api from "../../lib/api";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("../../lib/api", () => ({ get: jest.fn() }));
jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: () => ({user:{id:"staff",created_by:"owner"},loading:false}) }));
test.each(["fees","returns","exchanges"])("reads eight rows per request for the delegated %s page",async kind=>{
    globalThis.IS_REACT_ACT_ENVIRONMENT=true;
    api.get.mockReset().mockImplementation(async (url,{params})=>({data:{
        page:params.page,page_size:8,total_pages:2,items:[{id:"one",label:"سجل "+params.page,detail:"تفاصيل",status:"مؤكد"}],
    }}));
    const host=document.createElement("div"),root=createRoot(host);
    try {
        await act(async()=>root.render(<EmployeeBalanceSpecialist page={{id:"balances."+kind,label:"سجل"}}/>));
        expect(api.get).toHaveBeenCalledTimes(1);
        expect(host.textContent).toContain("سجل 1");
        await act(async()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="التالي").click());
        expect(api.get).toHaveBeenLastCalledWith("/operational-balances/workspace/specialist/"+kind,expect.objectContaining({params:{page:2}}));
        expect(host.textContent).toContain("سجل 2");
        expect([...host.querySelectorAll("button")].find(button=>button.textContent==="التالي").disabled).toBe(true);
    } finally {await act(async()=>root.unmount()); delete globalThis.IS_REACT_ACT_ENVIRONMENT;}
});

test.each([
    ["reports", "/operational-balances/financial-position", "?view=accounts", "accounts"],
    ["specialized", "/operational-balances", "?view=specialized&section=openings", "openings"],
    ["access", "/operational-balances/access", "", null],
])("direct %s link displays only the assigned specialist view", async (kind, pathname, search, group) => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const page = { id: "balances." + kind, mode: "read", to: pathname + (kind === "specialized" ? "?view=specialized" : ""), label: "تقرير الموظف" };
    expect(grantedPageAt([page], { pathname, search })).toBe(page);
    expect(grantedPageAt([], { pathname, search })).toBeNull();
    if (kind !== "access") expect(grantedPageAt([page], { pathname, search: search + "&" + (kind === "specialized" ? "section" : "view") + "=all" })).toBeNull();
    api.get.mockReset().mockResolvedValue({data:{items:[],page:1,page_size:8,total_pages:1,notice:"الحسابات غير مخصصة بعد"}});
    const host = document.createElement("div"), root = createRoot(host);
    try {
        await act(async () => root.render(<MemoryRouter><EmployeeBalanceSpecialist page={page} search={search}/></MemoryRouter>));
        expect(api.get).toHaveBeenCalledTimes(1);
        expect(api.get).toHaveBeenCalledWith("/operational-balances/workspace/specialist/" + kind,
            expect.objectContaining({params: {page: 1, ...(group ? {group} : {})}}));
        expect(host.textContent).toContain("الحسابات غير مخصصة بعد");
        expect(host.querySelector("form")).toBeNull();
    } finally { await act(async () => root.unmount()); delete globalThis.IS_REACT_ACT_ENVIRONMENT; }
});
