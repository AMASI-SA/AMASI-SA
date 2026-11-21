import React,{act} from "react";
import {createRoot} from "react-dom/client";
import InventoryMovementsView from "./InventoryMovementsView";
jest.mock("react-router-dom",()=>{global.TextEncoder=require("util").TextEncoder;return jest.requireActual("react-router");});
jest.mock("../../../context/AuthContext",()=>({useOptionalAuth:()=>null}));
jest.mock("../../../pages/operational/api",()=>({messageFor:()=>"تعذر قراءة الحركات"}));
const movement={id:"native-receipt",name:"بلوفر",sku:"MZ-1",occurred_at:"2026-11-20T09:00:00Z",source_type:"purchase_invoice",source_id:"INV-11",kind:"product",unit:"piece",quantity:4,direction:"in",warehouse_id:"ry",warehouse_name:"الرياض",location_code:"A1",unit_cost:null,value:null};
const response={schema_version:"g47-stocktake-v1",read_only:true,window_complete:true,items:[movement],warnings:[]};
let host,root,load;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement("div");document.body.appendChild(host);root=createRoot(host);load=jest.fn().mockResolvedValue(response);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const render=async(props={})=>act(async()=>root.render(<InventoryMovementsView allowed scope="native-session" loadMovements={load} {...props}/>));
test("movement reader preserves real source and unknown costs and opens read-only details",async()=>{
  await render();expect(load).toHaveBeenCalledTimes(1);expect(load.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal);
  expect(host.querySelector("tbody").textContent).toContain("INV-11");expect(host.querySelector("tbody").textContent).toContain("غير متاح");
  await act(async()=>host.querySelector('[aria-label="عرض حركة بلوفر"]').click());
  const dialog=document.querySelector('[role="dialog"]');expect(dialog.textContent).toContain("INV-11");expect(dialog.querySelector("form")).toBeNull();
});
test("permission alone controls reading and revocation clears data and dialogs",async()=>{
  await render({allowed:false});expect(load).not.toHaveBeenCalled();expect(host.querySelector("table")).toBeNull();
  await render();await act(async()=>host.querySelector('[aria-label="عرض حركة بلوفر"]').click());
  await render({allowed:false});expect(host.querySelector("table")).toBeNull();expect(document.querySelector('[role="dialog"]')).toBeNull();
});
test("incomplete window is explicit and never claims complete history",async()=>{
  load.mockResolvedValue({...response,window_complete:false});await render();expect(host.querySelector('[role="alert"]').textContent).toContain("لا تمثل سجل المخزون الكامل");
});
test("invalid movement contract fails closed and manual refresh can recover",async()=>{
  load.mockResolvedValueOnce({items:[movement]});await render();expect(host.querySelector("table")).toBeNull();expect(host.textContent).toContain("تعذر قراءة الحركات");
  await act(async()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="تحديث الحركات").click());expect(host.querySelector("table")).not.toBeNull();
});
test("movement filtering retains real zero costs and shows only the requested source row",async()=>{
  load.mockResolvedValue({...response,items:[movement,{...movement,id:"second",name:"قماش",sku:"CL-1",direction:"out",unit:"m",quantity:2,unit_cost:0,value:0}]});await render();
  const field=host.querySelector('[aria-label="بحث حركة المخزون"]');
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(field,"CL-1");field.dispatchEvent(new Event("input",{bubbles:true}));});
  expect(host.querySelectorAll("tbody tr")).toHaveLength(1);expect(host.querySelector("tbody").textContent).toContain("قماش");expect(host.querySelector("tbody").textContent).not.toContain("غير متاح");
});
