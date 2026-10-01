import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../../lib/api";
import AdvertisingBankMovement from "./AdvertisingBankMovement";
jest.mock("../../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
const account = { platform: "meta", integration_account_id: "native-ad", currency: "SAR", wallet_binding: "wallet", payable_binding: "payable" };
const movement = { id: "imported-proof", bank_account_id: "native-bank", amount: "100.00", currency: "SAR", direction: "out", status: "unclassified", source: "bank_statement_import", file_id: "file", file_hash: "hash", movement_date: "2026-01-02" };
let root, node;
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    api.get.mockResolvedValue({ data: { items: [movement] } });
    api.post.mockResolvedValue({ data: { status: "posted", txn_group_id: "native-journal" } });
});
afterEach(() => { act(() => root.unmount()); node.remove(); });
const mount = data => act(async () => root.render(<AdvertisingBankMovement account={data || account} />));
async function select(index,value) { await act(async () => {
    const input = node.querySelectorAll("select")[index];
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set.call(input,value);
    input.dispatchEvent(new Event("change",{bubbles:true}));
}); }
async function enter(index,value) { await act(async () => {
    const input = node.querySelectorAll("input")[index];
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,value);
    input.dispatchEvent(new Event("input",{bubbles:true}));
}); }
const submit = () => act(async () => node.querySelector("form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
test("missing identities cannot submit; funding uses exact imported bank facts",async () => {
    await mount(); await submit(); expect(api.post).not.toHaveBeenCalled();
    await select(0,"wallet_funding"); await select(1,"imported-proof"); await submit();
    expect(api.post).toHaveBeenCalledWith("/accounting-module/advertising-v2/bank-movement",{
        platform:"meta",integration_account_id:"native-ad",kind:"wallet_funding",bank_financial_account_id:"native-bank",
        bank_evidence_id:"imported-proof",amount_sar:"100.00",effective_at:"2026-01-02T00:00:00+03:00"
    });
    expect(node.textContent).toContain("native-journal");
    expect(node.querySelector('button[type="submit"]').disabled).toBe(true);
});
test("manual-only or claimed evidence is excluded and no bank defaults are invented",async () => {
    api.get.mockResolvedValue({data:{items:[{...movement,source:"manual_accountant"},{...movement,id:"missing-file",file_hash:null}]}});
    await mount(); await select(0,"wallet_funding"); await submit();
    expect(api.post).not.toHaveBeenCalled();
    expect(node.querySelectorAll("select")[1].options).toHaveLength(1);
    expect(node.textContent).toContain("استورد كشف البنك أولًا");
});
test("foreign funding requires explicit original amount and confirmed FX identity",async () => {
    await mount({...account,currency:"USD"}); await select(0,"wallet_funding"); await select(1,"imported-proof");
    await submit(); expect(api.post).not.toHaveBeenCalled();
    await enter(0,"25"); await enter(1,"a".repeat(64)); await submit();
    expect(api.post.mock.calls[0][1]).toMatchObject({original_wallet_currency_amount:"25",wallet_currency:"USD",fx_snapshot_id:"a".repeat(64)});
});
test("separate fee uses distinct same-bank evidence and preserves exact amounts",async () => {
    api.get.mockResolvedValue({data:{items:[movement,{...movement,id:"fee",amount:"5.00"},{...movement,id:"foreign-bank",bank_account_id:"other"}]}});
    await mount(); await select(0,"payable_settlement"); await select(1,"imported-proof");
    expect([...node.querySelectorAll("select")[2].options].map(x=>x.value)).toEqual(["","fee"]);
    await select(2,"fee"); await submit();
    expect(api.post.mock.calls[0][1]).toMatchObject({kind:"payable_settlement",bank_fee_sar:"5.00",bank_fee_evidence:"fee"});
});
test("backend paused or invalid proof is shown without success",async () => {
    api.post.mockRejectedValue({response:{status:423,data:{detail:{code:"accounting_writes_paused"}}}});
    await mount(); await select(0,"wallet_funding"); await select(1,"imported-proof"); await submit();
    expect(node.querySelector('[role="alert"]').textContent).toContain("accounting_writes_paused");
    expect(node.querySelector('[role="status"]')).toBeNull();
});
