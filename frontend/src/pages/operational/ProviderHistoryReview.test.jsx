import React, {act} from "react";
import {createRoot} from "react-dom/client";
import ProviderHistoryReview from "./ProviderHistoryReview";
import {operationalApi as api} from "./api";
jest.mock("./api", () => ({ operationalApi:{ providerHistoryReviews:jest.fn(), approveProviderHistory:jest.fn(), receipt:jest.fn() },
    messageFor: () => "تعذرت المطابقة", requestId: () => "stable-intent" }));
let host,root;
const fixture={order_id:"id",order_number:"123",provider:"tamara",paid:"100.00",remaining:"0.00",currency:"SAR",openings:[],movement_count:0,blocks:[],preview_hash:"hash",awaiting_refresh:false};
const button=t=>[...host.querySelectorAll("button")].find(b=>b.textContent.includes(t));
async function choose() {
    await act(async()=>button("مراجعة").click());
    const s=host.querySelector("select");
    await act(async()=>{s.value="id"; s.dispatchEvent(new Event("change",{bubbles:true}));});
}
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement("div");document.body.appendChild(host);root=createRoot(host);
   jest.resetAllMocks();api.providerHistoryReviews.mockResolvedValue({items:[fixture],session_scope:"scope"});
   api.receipt.mockResolvedValue({id:"evidence"});api.approveProviderHistory.mockResolvedValue({});
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test("read-only browse and approved one-order evidence do not trigger automatic approval or payment",async()=>{
    await act(async()=>root.render(<ProviderHistoryReview sessionScope="scope"/>));
    expect(api.providerHistoryReviews).not.toHaveBeenCalled(); await choose();
    expect(button("اعتماد").disabled).toBe(true);expect(api.approveProviderHistory).not.toHaveBeenCalled();
    const ref=host.querySelector("input:not([type])"),file=host.querySelector("input[type=file]");
    await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(ref,"statement S row 123");ref.dispatchEvent(new Event("input",{bubbles:true}));});
    await act(async()=>{Object.defineProperty(file,"files",{value:[new File(["proof"],"proof.pdf")]});file.dispatchEvent(new Event("change",{bubbles:true}));host.querySelector("input[type=checkbox]").click();});
    // Dispatch submit because jsdom does not provide native file-input validity for a synthetic FileList.
    await act(async()=>host.querySelector("form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
    expect(api.approveProviderHistory).toHaveBeenCalledWith({order_id:"id",expected_preview_hash:"hash",expected_session_scope:"scope",evidence_id:"evidence",statement_reference:"statement S row 123",request_id:"stable-intent",confirmed_outstanding:true});
    expect(host.textContent).toContain("بانتظار الاحتساب"); expect(host.querySelector("form")).toBeNull();
});
test("pooled payouts cannot be confirmed from the review UI",async()=>{
    api.providerHistoryReviews.mockResolvedValue({items:[{...fixture,blocks:["سداد مجمّع"]}],session_scope:"scope"});
    await act(async()=>root.render(<ProviderHistoryReview sessionScope="scope"/>));await choose();
    expect(host.textContent).toContain("سداد مجمّع");expect(host.querySelector("form")).toBeNull(); expect(api.receipt).not.toHaveBeenCalled();
});
