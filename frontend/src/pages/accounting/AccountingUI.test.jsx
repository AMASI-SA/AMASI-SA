import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { JournalTable } from "./AccountingUI";
let root,node;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;node=document.createElement("div");document.body.appendChild(node);root=createRoot(node);});
afterEach(()=>{act(()=>root.unmount());node.remove();});
const destination={destination_kind:"pos_receivable",entity_type:"asset",entity_id:"pos-identity",sub_account:"other_receivable",display_name:"جهاز الفرع"};
test.each(["destination","pos_destination"])("sealed %s identifies the POS receivable and canonical tuple",key=>{
    act(()=>root.render(<JournalTable rows={[{id:"leg",entity_type:"asset",entity_id:"pos-identity",sub_account:"other_receivable",side:"debit",amount:"500.00",metadata:{[key]:destination}}]}/>));
    const detail=node.querySelector('[data-testid="pos-movement-destination"]');
    expect(detail.textContent).toContain("ذمة شبكة POS: جهاز الفرع");
    expect(detail.textContent).toContain("asset/pos-identity/other_receivable");
    expect(node.textContent).toContain("500.00");
});
test("generic other receivables and POS-like names never infer a POS identity",()=>{
    act(()=>root.render(<JournalTable rows={[{entity_id:"unrelated",sub_account:"other_receivable",metadata:{destination:{...destination,destination_kind:"other_receivable",display_name:"POS"}}},{entity_id:"plain",sub_account:"other_receivable"}]}/>));
    expect(node.querySelector('[data-testid="pos-movement-destination"]')).toBeNull();
    expect(node.textContent).toContain("unrelated");expect(node.textContent).toContain("plain");
});
