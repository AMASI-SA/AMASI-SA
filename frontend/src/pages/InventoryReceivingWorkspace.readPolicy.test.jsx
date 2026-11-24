import React,{act} from "react";
import {createRoot} from "react-dom/client";
import InventoryReceivingWorkspace from "./InventoryReceivingWorkspace";
import api from "../lib/api";
import {loadInventoryReceivingCatalog,loadStockPreparationCatalog} from "../services/mezanInventoryReceiving";
import {syncRecentProductsV2} from "../services/mezanProductsV2";
jest.mock("../lib/api",()=>({__esModule:true,default:{get:jest.fn(),post:jest.fn()},formatApiErrorDetail:detail=>detail||""}));
jest.mock("react-router-dom",()=>({Link:({to,children,...props})=><a href={to} {...props}>{children}</a>}));
jest.mock("../context/AuthContext",()=>({useOptionalAuth:()=>({user:{is_owner:true}})}));
jest.mock("../services/mezanInventoryReceiving",()=>({loadInventoryReceivingCatalog:jest.fn(),loadStockPreparationCatalog:jest.fn()}));
jest.mock("../services/mezanProductsV2",()=>({syncRecentProductsV2:jest.fn(),refreshProductV2Details:jest.fn()}));
jest.mock("../components/inventory/SallaInventorySync",()=>()=>null);
let host,root;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;jest.clearAllMocks();loadInventoryReceivingCatalog.mockResolvedValue({recent_receipts:[{id:"receipt",product_name:"قطعة مثبتة",location_code:"A1",quantity:2}]});loadStockPreparationCatalog.mockResolvedValue({orders:[],suppliers:[],operators:[],permissions:{},retention_mode:{}});api.get.mockResolvedValue({data:{items:[]}});host=document.createElement("div");document.body.appendChild(host);root=createRoot(host);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test("readonly receiving reads receipts only and exposes no preparation, provider sync or invoice writer",async()=>{
  await act(async()=>root.render(<InventoryReceivingWorkspace accessMode="read"/>));
  expect(loadInventoryReceivingCatalog).toHaveBeenCalledTimes(1);expect(api.get).not.toHaveBeenCalled();expect(api.post).not.toHaveBeenCalled();
  expect(syncRecentProductsV2).not.toHaveBeenCalled();expect(loadStockPreparationCatalog).not.toHaveBeenCalled();
  expect(host.textContent).toContain("قطعة مثبتة");expect(host.querySelector('[aria-label="أقسام استلام المخزون"]')).toBeNull();expect(host.querySelector('.mz-receiving-workspace a[href="/purchase-invoices"]')).toBeNull();
});
test("opening receiving and its preparation tab never automatically synchronizes provider products",async()=>{
  await act(async()=>root.render(<InventoryReceivingWorkspace/>));
  expect(syncRecentProductsV2).not.toHaveBeenCalled();
  await act(async()=>[...host.querySelectorAll('[aria-label="أقسام استلام المخزون"] button')].find(button=>button.textContent.includes("تجهيز")).click());
  expect(loadStockPreparationCatalog).toHaveBeenCalledTimes(1);expect(syncRecentProductsV2).not.toHaveBeenCalled();expect(api.post).not.toHaveBeenCalled();
});
test("preparation location choices show short labels without changing warehouse scope or scanner identity",async()=>{
  loadInventoryReceivingCatalog.mockResolvedValue({locations:[
    {id:"loc-short",warehouse_id:"wh-1",display_code:"121",code:"WH01-S01-C01-002",barcode_value:"SCANNER-002",current_quantity:2},
    {id:"loc-legacy",warehouse_id:"wh-1",code:"OLD-009",current_quantity:0},
    {id:"loc-other",warehouse_id:"wh-2",display_code:"121",code:"OTHER-002",current_quantity:3},
  ]});
  loadStockPreparationCatalog.mockResolvedValue({permissions:{can_receive:true},orders:[{id:"order-1",reference:"PREP-1",status:"ready_for_receipt",destination_warehouse_id:"wh-1",destination_warehouse_name:"المخزن الرئيسي",items:[{id:"item-1",remaining_quantity:1}]}]});
  await act(async()=>root.render(<InventoryReceivingWorkspace/>));
  await act(async()=>[...host.querySelectorAll('[aria-label="أقسام استلام المخزون"] button')].find(button=>button.textContent.includes("تجهيز")).click());
  const preparation=host.querySelector('[data-testid="stock-preparation-orders"]'),select=preparation.querySelector('form select');
  expect(select.querySelector('option[value="loc-short"]').textContent).toBe("121 — حاليًا 2");
  expect(select.querySelector('option[value="loc-legacy"]').textContent).toBe("OLD-009 — حاليًا 0");expect(select.querySelector('option[value="loc-other"]')).toBeNull();expect(preparation.textContent).toContain("المخزن الرئيسي");
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set.call(select,"loc-short");select.dispatchEvent(new Event("change",{bubbles:true}));});
  expect(select.value).toBe("loc-short");expect(preparation.querySelector('input[placeholder="SCANNER-002"]')).not.toBeNull();expect(api.post).not.toHaveBeenCalled();
});
test("receiving refresh error clears old receipts and supports an explicit retry",async()=>{
  await act(async()=>root.render(<InventoryReceivingWorkspace accessMode="read"/>));
  loadInventoryReceivingCatalog.mockRejectedValueOnce(new Error("offline"));
  await act(async()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="تحديث").click());
  expect(host.textContent).not.toContain("قطعة مثبتة");expect(host.querySelector('[role="alert"]')).not.toBeNull();
  await act(async()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="إعادة التحميل").click());
  expect(host.textContent).toContain("قطعة مثبتة");
});

test("revoking receiving write mode ignores an older management invoice response",async()=>{
  let finishInventory,finishInvoices;
  loadInventoryReceivingCatalog.mockImplementationOnce(()=>new Promise(resolve=>{finishInventory=resolve;}));
  api.get.mockImplementationOnce(()=>new Promise(resolve=>{finishInvoices=resolve;}));
  await act(async()=>root.render(<InventoryReceivingWorkspace/>));
  await act(async()=>root.render(<InventoryReceivingWorkspace accessMode="read"/>));
  await act(async()=>{finishInventory({recent_receipts:[]});finishInvoices({data:{items:[{id:"old-owner",invoice_number:"OWNER-SECRET",schema_version:"g47-v1",state:"draft"}]}});});
  expect(host.textContent).not.toContain("OWNER-SECRET");expect(host.textContent).toContain("قطعة مثبتة");
  expect(host.querySelector('[data-testid="purchase-approval-entry"]')).toBeNull();
});
