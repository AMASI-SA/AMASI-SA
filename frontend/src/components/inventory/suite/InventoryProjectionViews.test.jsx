import React, {act} from "react";
import {createRoot} from "react-dom/client";
import {InventoryHomeProjection,InventoryReportsProjection,summarizeInventoryProjection} from "./InventoryProjectionViews";

const base={id:"one",name:"بلوفر",sku:"P-1",warehouse_id:"riyadh",warehouse_name:"الرياض",location_code:"A1",unit:"piece",physical:"10",reserved:"2",available:"8",held:"0",inventory_value:"200",specifications:{}};
test("reports aggregate only identical warehouse and native unit while incomplete values remain unknown",()=>{
  const summary=summarizeInventoryProjection([base,{...base,id:"two",physical:"3",reserved:null,available:null,inventory_value:null},{...base,id:"cloth",unit:"m",physical:"4",reserved:"0",available:"4",inventory_value:"60"},{...base,id:"jeddah",warehouse_id:"jeddah",warehouse_name:"جدة",physical:"2",reserved:"0",available:"2",inventory_value:"40"}]);
  expect(summary.recordCount).toBe(4);expect(summary.warehouseCount).toBe(2);expect(summary.groups).toHaveLength(3);
  const riyadhPieces=summary.groups.find(group=>group.warehouse_name==="الرياض"&&group.unit==="piece");
  expect(riyadhPieces.physical).toBe(13);expect(riyadhPieces.reserved).toBeNull();expect(riyadhPieces.available).toBeNull();expect(riyadhPieces.inventory_value).toBeNull();expect(riyadhPieces.unknownCostRows).toBe(1);
  expect(summary.reviewRows.map(row=>row.id)).toEqual(["two"]);
});
test("report preserves zero valuation as known and missing values as unknown",()=>{
  const summary=summarizeInventoryProjection([{...base,inventory_value:"0",available:"0",held:"10"}]);
  expect(summary.groups[0].inventory_value).toBe(0);expect(summary.groups[0].unknownCostRows).toBe(0);expect(summary.heldRecordCount).toBe(1);
  expect(summarizeInventoryProjection([{...base,inventory_value:""}]).groups[0].inventory_value).toBeNull();
});
async function withView(view,assertion){global.IS_REACT_ACT_ENVIRONMENT=true;const host=document.createElement("div");document.body.appendChild(host);const root=createRoot(host);try{await act(async()=>root.render(view));await assertion(host);}finally{await act(async()=>root.unmount());host.remove();}}
test("daily review links the real ambiguous balance without showing invented purchase tasks",async()=>{
  const onNavigate=jest.fn();
  await withView(<InventoryHomeProjection result={{items:[{...base,available:null,availability_issues:["reservation"]}]}} context={{permissions:{reports:true}}} onNavigate={onNavigate}/>,async(host)=>{
    expect(host.textContent).toContain("أعمال تحتاج مراجعة");expect(host.textContent).not.toContain("إضافة مخزون موجود");
    await act(async()=>host.querySelector(".mz-stock-review-row").click());
    expect(onNavigate).toHaveBeenCalledWith("stock","one");
  });
});
test("aggregate report makes incomplete source valuation visible in the actual table",async()=>{
  await withView(<InventoryReportsProjection result={{items:[{...base,inventory_value:null}]}}/>,async(host)=>{
    const cells=[...host.querySelectorAll("tbody td")].map(cell=>cell.textContent);
    expect(cells).toContain("غير متاح");expect(host.textContent).toContain("سجل بدون تقييم");
    expect(host.querySelector("input")).toBeNull();expect(host.querySelector("form")).toBeNull();
  });
});
