import React, { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import TikTokCaptionComposer from "./TikTokCaptionComposer";
import TikTokFilePublishing from "./TikTokFilePublishing";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
const result = { copy: { organic: { caption: "هدية تقول اللي في قلبك 🤍\n#هودي #هدايا #السعودية" }, promoted: { caption: "اختر هدية لها معنى\n#أماسي #هدايا #السعودية" }, opening_tip: "ابدأ بلقطة قريبة لتفاصيل المنتج." } };
let host, root, serial;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset(); serial = 0;
    Object.defineProperty(window,"crypto",{ configurable:true,value:{ randomUUID:()=>`caption-request-${++serial}-12345678` } });
    host=document.createElement("div");document.body.appendChild(host);root=createRoot(host);globalThis.IS_REACT_ACT_ENVIRONMENT=true;
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const button=(text)=>[...host.querySelectorAll("button")].find((node)=>node.textContent===text);
async function fill(label,value) {
    const node=host.querySelector(`[aria-label="${label}"]`), prototype=node.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    await act(async()=>{Object.getOwnPropertyDescriptor(prototype,"value").set.call(node,value);node.dispatchEvent(new Event("input",{bubbles:true}));});
}
async function selectProduct() {
    await fill("البحث عن منتج فيديو TikTok","هودي (شتاء)+");
    api.get.mockResolvedValueOnce({data:{items:[{salla_product_id:"123",name:"هودي هدية"}]}});
    await act(async()=>button("بحث منتج الفيديو").click());
    expect(api.get.mock.calls[0][1].params.q).toBe("هودي \\(شتاء\\)\\+");
    await act(async()=>button("هودي هدية").click());
    await fill("فكرة فيديو TikTok","هدية للشتاء");
}

test("mount does not bill; product selection produces two reviewable captions, never a native publish",async()=>{
    const apply=jest.fn();await act(async()=>root.render(<StrictMode><TikTokCaptionComposer onApply={apply}/></StrictMode>));
    expect(api.get).not.toHaveBeenCalled();expect(api.post).not.toHaveBeenCalled();
    await selectProduct();api.post.mockResolvedValueOnce({data:result});
    await act(async()=>button("تجهيز وصف تسويقي للسعودية").click());
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/tiktok/content/captions/proposals");
    expect(api.post.mock.calls[0][1]).toMatchObject({product_id:"123",brief:"هدية للشتاء"});
    expect(host.querySelectorAll("article")).toHaveLength(2);expect(apply).not.toHaveBeenCalled();
    await act(async()=>host.querySelector("article button").click());
    expect(apply).toHaveBeenCalledWith(result.copy.organic.caption);expect(api.post).toHaveBeenCalledTimes(1);
});

test("an ambiguous model response retries the same saved intent only on an explicit click",async()=>{
    await act(async()=>root.render(<TikTokCaptionComposer onApply={jest.fn()}/>));await selectProduct();
    api.post.mockRejectedValueOnce(new Error("timeout"));await act(async()=>button("تجهيز وصف تسويقي للسعودية").click());
    expect(api.post).toHaveBeenCalledTimes(1);expect(host.querySelector('[role="alert"]')).toBeTruthy();
    api.post.mockResolvedValueOnce({data:result});await act(async()=>button("تجهيز وصف تسويقي للسعودية").click());
    expect(api.post.mock.calls[0][1].idempotency_key).toBe(api.post.mock.calls[1][1].idempotency_key);
    api.post.mockResolvedValueOnce({data:result});await act(async()=>button("اقتراح جديد").click());
    expect(api.post.mock.calls[2][1].idempotency_key).not.toBe(api.post.mock.calls[0][1].idempotency_key);
});

test("applying copy fills the file caption and own-business disclosure without marking video as AI or publishing",async()=>{
    await act(async()=>root.render(<TikTokFilePublishing/>));await selectProduct();api.post.mockResolvedValueOnce({data:result});
    await act(async()=>button("تجهيز وصف تسويقي للسعودية").click());await act(async()=>host.querySelector("article button").click());
    expect(host.querySelector('[aria-label="نص منشور ملف TikTok"]').value).toBe(result.copy.organic.caption);
    expect(host.querySelector('[aria-label="إفصاح ملف TikTok"]').value).toBe("own_business");
    expect(host.querySelector('[aria-label="الفيديو مولّد بالذكاء الاصطناعي"]').checked).toBe(false);
    expect(api.post).toHaveBeenCalledTimes(1);expect(button("اعتماد رفع الملف ونشره")).toBeUndefined();
});
