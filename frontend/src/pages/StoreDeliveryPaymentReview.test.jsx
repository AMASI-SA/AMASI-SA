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

const fact = {id:"pos-fact",entity_id:"pos-fact",display_name:"جهاز الفرع",category:"other_receivable",entity_type:"asset",sub_account:"other_receivable",status:"active",currency:"SAR",source:"documented_opening_fact"};
const posItem = {...item,payment_method:"card_terminal",receipt_reference:"bound-receipt",receipt_url:"/api/store-delivery/evidence/receipt/bound-receipt"};
function mockPos(rows=[fact], review=posItem) {
    api.get.mockImplementation(url=>Promise.resolve({data:url.endsWith("typed-facts")?{items:rows}:url.endsWith("bank-accounts")?{source:"mz2_financial_accounts",items:[]}:{items:[review]}}));
}
async function change(label,value) {
    await act(async()=>{
        const field=[...node.querySelectorAll("article label")].find(row=>row.textContent.startsWith(label)).querySelector("select,input");
        const prototype=field.tagName==="SELECT"?HTMLSelectElement.prototype:HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype,"value").set.call(field,value);
        field.dispatchEvent(new Event(field.tagName==="SELECT"?"change":"input",{bubbles:true}));
    });
}
test.each(["", "POS-transaction-42"])("POS accountant approves explicit documented identity and exact receipt amount with reference '%s'",async reference=>{
    mockPos();await mount();
    expect(api.get).toHaveBeenCalledWith("/accounting-module/onboarding/typed-facts");
    expect(node.querySelector("article select").value).toBe("");
    expect(approve().disabled).toBe(true);
    expect(node.querySelector("article a").getAttribute("href")).toBe(posItem.receipt_url);
    expect(node.textContent).toContain("جهاز الفرع · asset/pos-fact/other_receivable");
    expect(node.textContent).toContain("لا يثبت وصول المبلغ إلى البنك");
    await change("ذمة الشبكة المختارة","pos-fact");expect(approve().disabled).toBe(true);
    await change("مبلغ إيصال الشبكة المطابق","500.00");
    await change("مرجع عملية الشبكة",reference);
    expect(approve().disabled).toBe(false);
    expect(api.post).not.toHaveBeenCalled();await act(async()=>approve().click());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/store-delivery/payment-review/assignment',{
        decision:"approved",note:"تمت المطابقة",destination_financial_id:"pos-fact",pos_reviewed_amount:"500.00",
        ...(reference?{settlement_reference:reference}:{})
    });
});
test.each(["", "499.99", "500.001", "5e2", "500junk", "500,00", "-500", "Infinity", " 500 ", "0500", "0"])("POS rejects malformed or unmatched amount '%s'",async amount=>{
    mockPos();await mount();await change("ذمة الشبكة المختارة","pos-fact");await change("مبلغ إيصال الشبكة المطابق",amount);
    expect(approve().disabled).toBe(true);await act(async()=>approve().click());expect(api.post).not.toHaveBeenCalled();
});
test.each(["receipt_url","receipt_reference"])("POS requires bound receipt field %s",async field=>{
    mockPos([fact],{...posItem,[field]:""});await mount();await change("ذمة الشبكة المختارة","pos-fact");await change("مبلغ إيصال الشبكة المطابق","500");
    expect(approve().disabled).toBe(true);expect(node.textContent).toContain("يلزم إيصال مرتبط");
    await act(async()=>approve().click());expect(api.post).not.toHaveBeenCalled();
});
test("POS options exclude invalid canonical facts without guessing labels or selecting defaults",async()=>{
    const invalid=[{status:"inactive"},{currency:"USD"},{source:"legacy"},{category:"other_payable"},{entity_type:"liability"},{sub_account:"payable"},{entity_id:"wrong"},{id:""},{display_name:""}].map((patch,index)=>({...fact,id:`invalid-${index}`,entity_id:`invalid-${index}`,...patch}));
    mockPos([...invalid,fact]);await mount();
    expect([...node.querySelector("article select").options].map(row=>row.value)).toEqual(["","pos-fact"]);
    expect(approve().disabled).toBe(true);expect(api.post).not.toHaveBeenCalled();
});
test("unavailable facts fail closed while receipt rejection retains original payload and cancellation",async()=>{
    mockPos();const get=api.get.getMockImplementation();api.get.mockImplementation(url=>url.endsWith("typed-facts")?Promise.reject(new Error("offline")):get(url));
    await mount();expect(approve().disabled).toBe(true);expect(node.textContent).toContain("تعذر تحميل الذمم الأصلية");
    const prompt=jest.spyOn(window,"prompt").mockReturnValueOnce(null).mockReturnValueOnce("المبلغ غير مطابق");
    const reject=[...node.querySelectorAll("button")].find(button=>button.textContent.includes("رفض"));
    await act(async()=>reject.click());expect(api.post).not.toHaveBeenCalled();
    await act(async()=>reject.click());expect(api.post).toHaveBeenCalledWith('/store-delivery/payment-review/assignment',{decision:"rejected",note:"المبلغ غير مطابق"});prompt.mockRestore();
});
