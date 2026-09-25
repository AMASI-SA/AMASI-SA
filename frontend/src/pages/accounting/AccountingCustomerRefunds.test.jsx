import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingCustomerRefunds from "./AccountingCustomerRefunds";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
let node,root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT=true;
    node=document.createElement("div");document.body.appendChild(node);root=createRoot(node);
    api.get.mockResolvedValue({data:{originals:[],banks:[],cases:[{id:"case",case_reference:"SYN",order_number:"SYN-order",original_provider:"emkan",amount:"50.00",paid:"30.00",remaining:"20.00",state:"partially_paid"}],payments:[{id:"pay",case_reference:"SYN",execution_channel:"bank",bank_account_name:"SYN bank",amount:"30.00",bank_reference:"SYN-ref",status:"awaiting_entitlement_and_approval"}]}});
});
afterEach(() => {act(()=>root.unmount());node.remove();jest.clearAllMocks();});
test("viewer sees requested paid remaining and separate original/execution channels with no write actions",async()=>{
    await act(async()=>root.render(<AccountingCustomerRefunds accountingPermissions={["accounting.movements.view"]}/>));
    expect(node.textContent).toContain("50.00");expect(node.textContent).toContain("30.00");expect(node.textContent).toContain("20.00");
    expect(node.textContent).toContain("إمكان");expect(node.textContent).toContain("SYN bank");
    expect(node.querySelector("select")).toBeNull();expect(node.textContent).not.toContain("اعتماد حركة الاسترداد المنفذة");expect(api.post).not.toHaveBeenCalled();
});
test("entry permission exposes draft selection and all execution channels without approval",async()=>{
    await act(async()=>root.render(<AccountingCustomerRefunds accountingPermissions={["accounting.refunds.create"]}/>));
    expect(node.querySelector('[aria-label="اختيار مسودة الاسترداد"]')).not.toBeNull();
    expect(node.querySelector('[aria-label="جهة تنفيذ الاسترداد"]').options.length).toBe(5);
    expect(node.textContent).not.toContain("اعتماد حركة الاسترداد المنفذة");expect(api.post).not.toHaveBeenCalled();
});
