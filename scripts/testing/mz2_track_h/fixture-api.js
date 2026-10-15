// Synthetic review transport only. Vite review config aliases the real api import here.
// Never loaded by the production application. Unknown reads and every write fail closed.
import { fixture as advertisingFixture } from "./h2-advertising-fixture.js";
import driverFixture from "./h2-driver-fixture.js";
import { fixture as obligationsFixture } from "./h2-obligations-fixture.js";
const date="2026-09-30";
const journalItems=[
{id:"j1d",txn_group_id:"SYN-JOURNAL-001",entity_type:"supplier",entity_id:"SYN-SUPPLIER-1",sub_account:"advance",side:"debit",amount:2500,effective_at:date+"T09:00:00+03:00",entry_type:"supplier_payment",metadata:{evidence_ref:"SYN-BANK-001"}},
{id:"j1c",txn_group_id:"SYN-JOURNAL-001",entity_type:"bank",entity_id:"SYN-BANK-1",sub_account:"main",side:"credit",amount:2500,effective_at:date+"T09:00:00+03:00",entry_type:"supplier_payment",metadata:{evidence_ref:"SYN-BANK-001"}},
{id:"j2d",txn_group_id:"SYN-JOURNAL-002",entity_type:"tax",entity_id:"SYN-TAX",sub_account:"input_vat",side:"debit",amount:150,effective_at:date+"T11:00:00+03:00",entry_type:"purchase_tax",metadata:{evidence_ref:"SYN-INV-002"}},
{id:"j2c",txn_group_id:"SYN-JOURNAL-002",entity_type:"supplier",entity_id:"SYN-SUPPLIER-1",sub_account:"payable",side:"credit",amount:150,effective_at:date+"T11:00:00+03:00",entry_type:"purchase_tax",metadata:{evidence_ref:"SYN-INV-002"}}
];
const trial=[
{entity_type:"supplier",entity_id:"SYN-SUPPLIER-1",sub_account:"advance",debits:2500,credits:0,net:2500},
{entity_type:"supplier",entity_id:"SYN-SUPPLIER-1",sub_account:"payable",debits:1000,credits:8000,net:-7000},
{entity_type:"employee",entity_id:"SYN-EMPLOYEE-1",sub_account:"salary_payable",debits:2000,credits:3000,net:-1000},
{entity_type:"ad_account",entity_id:"SYN-AD-PREPAID",sub_account:"balance",debits:5000,credits:600,net:4400},
{entity_type:"ad_account",entity_id:"SYN-AD-PAYABLE",sub_account:"debt",debits:0,credits:800,net:-800},
{entity_type:"tax",entity_id:"SYN-TAX",sub_account:"input_vat",debits:150,credits:0,net:150}
];
const movementItems=[
{id:"SYN-MOVE-1",movement_date:date,direction:"out",amount:2500,description:"دفعة مقدمة موثقة",reference:"SYN-BANK-001",status:"accounting_posted"},
{id:"SYN-MOVE-2",movement_date:date,direction:"in",amount:4250.75,description:"تحويل وارد للمراجعة",reference:"SYN-BANK-002",status:"unclassified"},
{id:"SYN-MOVE-3",movement_date:"2026-09-29",direction:"out",amount:null,description:"دليل ناقص — المبلغ غير متاح",reference:"SYN-BANK-003",status:"needs_review"}
];
const settlements=[
{id:"SYN-SETTLEMENT-1",provider:"tabby",provider_label:"تابي",statement_reference:"SYN-ST-001",status:"draft",currency:"SAR",bank_account_name:"حساب اختبار",period_from:"2026-09-01",period_to:date,reported_net:14668.53,source_filename:"synthetic-tabby.xlsx",review_reasons:[],amounts:{gross_sales:15000,reported_net:14668.53,commission:288.23,commission_vat:43.24}},
{id:"SYN-SETTLEMENT-2",provider:"tamara",provider_label:"تمارا",statement_reference:"SYN-ST-002",status:"needs_review",currency:"SAR",bank_account_name:"حساب اختبار",period_from:"2026-09-01",period_to:date,reported_net:4151.97,review_reasons:[{code:"evidence_required",message:"دليل الحركة غير مكتمل"}],amounts:{gross_sales:4300,reported_net:4151.97}}
];
function failWrite(){throw new Error("Synthetic review: financial writes are disabled");}
export default {
async get(url,{params={}}={}){
 const state=new URLSearchParams(window.location.search).get("state");
 if(state==="loading") return new Promise(()=>{});
 if(state==="error") throw new Error("Synthetic read failure");
 const empty=state==="empty";
 const h2data = advertisingFixture(url, params) ?? driverFixture(url, params) ?? obligationsFixture(url, params);
 if(h2data !== undefined){
   if(state === "unavailable") throw { response: { status:404, data:{ detail:{code:"synthetic_native_route_not_integrated"} } } };
   return {data: empty ? {...h2data, ...(Array.isArray(h2data.items)?{items:[]}:{}), ...(h2data.store_drivers?{store_drivers:[],couriers:[]}: {})} : h2data};
 }
 const prefix="/financial-provider-apps/accounting-module";
 if(!url.startsWith(prefix))throw new Error("Unapproved synthetic read: "+url);
 const endpoint=url.slice(prefix.length);
 let data;
 if(endpoint.startsWith("/reports/")){
 const common={status:state==="unavailable"?"not_ready":"available",reason:"cutover_not_approved",ledger_backend:"v2",operation_id:"MZ2-FIN-CUTOVER-001",cutover_at:"2026-09-01T00:00:00+03:00",opening_balance_txn_group_id:"SYN-OPENING",as_of:params.as_of||null};
 data=endpoint.endsWith("financial-position")?{...common,assets:{banks:125000,supplier_advance:2500,ad_account_prepaid:4400,input_vat:150},liabilities:{supplier_payable:7000,salaries_unpaid:1000,ad_accounts_unpaid:800,sales_vat_payable:3200},totals:{total_assets:132050,total_liabilities:12000,net_position:120050}}:{...common,items:empty?[]:endpoint.endsWith("journals")?journalItems:trial};
 }else if(endpoint==="/daily-movements/context")data={banks:[],suppliers:[],expense_categories:[]};
 else if(endpoint==="/daily-movements")data={items:empty?[]:movementItems};
 else if(endpoint==="/settlements/context")data={banks:[],bindings:[],status_counts:{}};
 else if(endpoint==="/settlements/drafts")data={items:empty?[]:settlements};
 else if(endpoint==="/settlements/register")data={items:empty?[]:settlements.filter(r=>(!params.provider||r.provider===params.provider)&&(!params.q||r.statement_reference.includes(params.q))),total_filtered:empty?0:2};
 else if(endpoint.startsWith("/settlements/register/")){const draft=settlements.find(r=>endpoint.endsWith(r.id));data={draft,register_item:draft,evidence:{entries:[],entry_count:0,file_locked:true},ledger:{status:"not_ready",reason:"settlement_not_posted",entries:[]}};}
 else if(endpoint==="/bank-receipts")data={items:[]};
 else if(endpoint==="/order-recognition/queue")data={items:[],waiting_count:0};
 else if(endpoint==="/shipping-p02/workspace")data={latest_rates:[],counterparties:[],bank_movements:[],recent_events:[],courier_candidates:empty?[]:[{id:"SYN-SHIP-1",order_number:"SYN-ORDER-001",shipping_company:"شركة شحن تجريبية",waybill:"SYN-WAYBILL-01",delivery_source_text:date+"T13:00:00+03:00",shipping_cost_source:24.5}],driver_candidates:empty?[]:[{assignment_id:"SYN-DRIVER-ASSIGNMENT",order_number:"SYN-ORDER-002",driver_name:"موصل تجريبي",cod_custody_amount:180,collected_at:date+"T13:00:00+03:00"}]};
 else throw new Error("Unapproved synthetic read: "+endpoint);
 return {data};
}, post:failWrite,put:failWrite,patch:failWrite,delete:failWrite
};
