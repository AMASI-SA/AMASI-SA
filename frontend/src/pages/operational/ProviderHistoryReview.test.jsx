import React, {act} from "react";
import {createRoot} from "react-dom/client";
import ProviderHistoryReview from "./ProviderHistoryReview";
import {operationalApi as api} from "./api";
jest.mock("./api", () => ({ operationalApi:{ providerHistoryReviews:jest.fn(), approveProviderHistory:jest.fn(), receipt:jest.fn() },
    messageFor: () => "تعذرت المطابقة", requestId: () => "stable-intent" }));
let host,root;
const visibility=async value=>{
    Object.defineProperty(document,'visibilityState',{configurable:true,value});
    await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
};
const fixture={order_id:"id",order_number:"123",provider:"tamara",paid:"100.00",remaining:"0.00",currency:"SAR",openings:[],movement_count:0,blocks:[],preview_hash:"hash",awaiting_refresh:false};
const button=t=>[...host.querySelectorAll("button")].find(b=>b.textContent.includes(t));
async function choose() {
    await act(async()=>button("مراجعة").click());
    const s=host.querySelector("select");
    await act(async()=>{s.value="id"; s.dispatchEvent(new Event("change",{bubbles:true}));});
}
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});host=document.createElement("div");document.body.appendChild(host);root=createRoot(host);
   jest.resetAllMocks();api.providerHistoryReviews.mockResolvedValue({items:[fixture],session_scope:"scope"});
   api.receipt.mockResolvedValue({id:"evidence"});api.approveProviderHistory.mockResolvedValue({});
});
afterEach(async()=>{if(root)await act(async()=>root.unmount());host.remove();delete document.visibilityState;});
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

test('provider history waits while hidden and never repeats a completed read on focus',async()=>{
    await visibility('hidden');
    await act(async()=>root.render(<ProviderHistoryReview sessionScope="scope"/>));
    expect(api.providerHistoryReviews).not.toHaveBeenCalled();
    await act(async()=>button('مراجعة').click());
    expect(api.providerHistoryReviews).not.toHaveBeenCalled();
    await visibility('visible');
    expect(api.providerHistoryReviews).toHaveBeenCalledWith({signal:expect.any(AbortSignal)});
    expect(host.querySelector('select')).not.toBeNull();
    await visibility('hidden');await visibility('visible');
    await act(async()=>window.dispatchEvent(new Event('focus')));
    expect(api.providerHistoryReviews).toHaveBeenCalledTimes(1);
});

test.each(['hidden','unmount','scope'])('provider history aborts on %s and cannot admit a late previous result',async departure=>{
    let resolve;
    api.providerHistoryReviews.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
    await act(async()=>root.render(<ProviderHistoryReview sessionScope="scope"/>));
    await act(async()=>button('مراجعة').click());
    const signal=api.providerHistoryReviews.mock.calls[0][0].signal;
    if(departure==='hidden')await visibility('hidden');
    else if(departure==='scope')await act(async()=>root.render(<ProviderHistoryReview sessionScope="other"/>));
    else {await act(async()=>root.unmount());root=null;}
    expect(signal.aborted).toBe(true);
    await act(async()=>resolve({items:[fixture],session_scope:'scope'}));
    expect(host.querySelector('select')).toBeNull();
    expect(api.providerHistoryReviews).toHaveBeenCalledTimes(1);
});

test('a hidden provider read resumes only visibly after the old request settles',async()=>{
    let resolve;
    api.providerHistoryReviews.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
    await act(async()=>root.render(<ProviderHistoryReview sessionScope="scope"/>));
    await act(async()=>button('مراجعة').click());
    await visibility('hidden');await visibility('visible');
    expect(api.providerHistoryReviews).toHaveBeenCalledTimes(1);
    await act(async()=>resolve({items:[fixture],session_scope:'scope'}));
    expect(api.providerHistoryReviews).toHaveBeenCalledTimes(2);
    expect(host.querySelector('select')).not.toBeNull();
});

test.each(['hidden','unmount'])('submitted provider proof and approval remain durable after %s without new reads',async departure=>{
    await act(async()=>root.render(<ProviderHistoryReview sessionScope="scope"/>));await choose();
    const ref=host.querySelector('input:not([type])'),file=host.querySelector('input[type=file]');
    await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(ref,'statement row 123');ref.dispatchEvent(new Event('input',{bubbles:true}));});
    await act(async()=>{Object.defineProperty(file,'files',{value:[new File(['proof'],'proof.pdf')]});file.dispatchEvent(new Event('change',{bubbles:true}));host.querySelector('input[type=checkbox]').click();});
    let resolve;
    api.receipt.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
    await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    expect(api.receipt.mock.calls[0]).toHaveLength(1);
    if(departure==='hidden')await visibility('hidden');
    else {await act(async()=>root.unmount());root=null;}
    await act(async()=>resolve({id:'evidence'}));
    expect(api.approveProviderHistory).toHaveBeenCalledWith(expect.objectContaining({order_id:'id',expected_session_scope:'scope',evidence_id:'evidence'}));
    expect(api.approveProviderHistory.mock.calls[0]).toHaveLength(1);
    if(departure==='hidden')await visibility('visible');
    expect(api.providerHistoryReviews).toHaveBeenCalledTimes(1);
});
