import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import StoreDeliveryPaymentReview from "./StoreDeliveryPaymentReview";
jest.mock("../lib/api",()=>({get:jest.fn(),post:jest.fn()}));
jest.mock("sonner",()=>({toast:{success:jest.fn(),error:jest.fn()}}));
let root,node;
const item = {assignment_id:"assignment",payment_method:"bank_transfer",amount:"500",order_number:"1"};
beforeEach(()=>{
    jest.resetAllMocks();global.IS_REACT_ACT_ENVIRONMENT=true;
    node=document.createElement("div");document.body.appendChild(node);root=createRoot(node);
    api.get.mockImplementation(url=>Promise.resolve({data:url.endsWith("bank-accounts")?{source:"mz2_financial_accounts",items:[{id:"canonical-bank",name:"Native bank"}]}:{items:[item]}}));
    api.post.mockResolvedValue({data:{state:"posted"}});
});
afterEach(()=>{act(()=>root.unmount());node.remove();});
const mount=()=>act(async()=>root.render(<StoreDeliveryPaymentReview/>));
const approve=()=>[...node.querySelectorAll("button")].find(button=>button.textContent.includes("اعتماد مدفوع"));
test("bank approval requires canonical destination and imported movement id",async()=>{
    await mount();expect(approve().disabled).toBe(true);expect(api.post).not.toHaveBeenCalled();
    await act(async()=>{
        const select=node.querySelector('article select');
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set.call(select,"canonical-bank");
        select.dispatchEvent(new Event("change",{bubbles:true}));
        const input=node.querySelector('article input');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,"imported-movement-id");
        input.dispatchEvent(new Event("input",{bubbles:true}));
    });
    expect(approve().disabled).toBe(false);
    await act(async()=>approve().click());
    expect(api.post).toHaveBeenCalledWith('/store-delivery/payment-review/assignment',{
        decision:"approved",note:"تمت المطابقة",destination_financial_id:"canonical-bank",settlement_reference:"imported-movement-id"
    });
});
test("POS receipt does not unlock approval without processor success proof",async()=>{
    api.get.mockResolvedValue({data:{items:[{...item,payment_method:"card_terminal"}]}});
    await mount();expect(approve().disabled).toBe(true);
    expect(node.textContent).toContain("صورة الإيصال لا تكفي");
    await act(async()=>approve().click());expect(api.post).not.toHaveBeenCalled();
});
