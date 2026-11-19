import React, { act } from "react";
import { createRoot } from "react-dom/client";
import EmployeeBalanceSpecialist from "./EmployeeBalanceSpecialist";
import api from "../../lib/api";

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
