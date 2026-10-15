import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../../lib/api";
import AdvertisingZeroOpening from "./AdvertisingZeroOpening";
jest.mock("../../../lib/api",()=>({post:jest.fn()}));
const account={platform:"meta",integration_account_id:"native-ad",currency:"USD",wallet_binding:"native-wallet"};
let root,node,confirmed;
beforeEach(()=>{jest.resetAllMocks();global.IS_REACT_ACT_ENVIRONMENT=true;
    node=document.createElement("div");document.body.appendChild(node);root=createRoot(node);confirmed=jest.fn();
    api.post.mockResolvedValue({data:{id:"zero-evidence",zero_original_confirmed:true,confirmed_by:"owner"}});
});
afterEach(()=>{act(()=>root.unmount());node.remove();});
const mount=()=>act(async()=>root.render(<AdvertisingZeroOpening account={account} onConfirmed={confirmed}/>));
async function enter(index,value){await act(async()=>{const input=node.querySelectorAll('input:not([type="checkbox"])')[index];
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,value);
    input.dispatchEvent(new Event("input",{bubbles:true}));});}
const submit=()=>act(async()=>node.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
async function fill(){await enter(0,'0');await enter(1,'native-opening-group');await enter(2,'2026-01-01T00:00:00+03:00');await enter(3,'Provider original zero wallet statement');}
test('never infers original zero or FX and requires explicit owner checkbox',async()=>{
    await mount();await submit();expect(api.post).not.toHaveBeenCalled();
    await fill();await submit();expect(api.post).not.toHaveBeenCalled();
    await act(async()=>node.querySelector('input[type="checkbox"]').click());await submit();
    expect(api.post).toHaveBeenCalledWith('/accounting-module/advertising-v2/wallet-opening-evidence',{
        platform:'meta',integration_account_id:'native-ad',currency:'USD',original_currency_amount:'0',opening_sar_amount:'0.00',
        zero_original_confirmed:true,opening_txn_group_id:'native-opening-group',effective_at:'2026-01-01T00:00:00+03:00',
        evidence:'Provider original zero wallet statement'
    });expect(confirmed).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).not.toHaveProperty('fx_snapshot_id');
});
test('positive original units and naive times cannot use zero confirmation',async()=>{
    await mount();await fill();await act(async()=>node.querySelector('input[type="checkbox"]').click());
    await enter(0,'10');await submit();expect(api.post).not.toHaveBeenCalled();
    await enter(0,'0');await enter(2,'2026-01-01T00:00:00');await submit();expect(api.post).not.toHaveBeenCalled();
});
test('backend missing exact native zero manifest stays blocked',async()=>{
    api.post.mockRejectedValue({response:{status:409,data:{detail:{code:'ad_wallet_approved_zero_manifest_required'}}}});
    await mount();await fill();await act(async()=>node.querySelector('input[type="checkbox"]').click());await submit();
    expect(node.querySelector('[role="alert"]').textContent).toContain('ad_wallet_approved_zero_manifest_required');expect(confirmed).not.toHaveBeenCalled();
});
