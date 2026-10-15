import React from "react";
import {createRoot} from "react-dom/client";
import {BrowserRouter,useSearchParams} from "react-router-dom";
import AccountingDailyWorkspace from "../../../frontend/src/pages/accounting/AccountingDailyWorkspace.jsx";
import AccountingDailyMovements from "../../../frontend/src/pages/accounting/AccountingDailyMovements.jsx";
import AccountingReports from "../../../frontend/src/pages/accounting/AccountingReports.jsx";
import AccountingSettlementRegister from "../../../frontend/src/pages/accounting/AccountingSettlementRegister.jsx";
import AccountingShippingCod from "../../../frontend/src/pages/accounting/AccountingShippingCod.jsx";
import {PartialWorkflowPage} from "../../../frontend/src/pages/accounting/AccountingWorkflowPages.jsx";
import {AccountingHeader} from "../../../frontend/src/pages/accounting/AccountingShared.jsx";
import "./styles.css";
const names={home:"لوحة المحاسبة",daily:"الحركات اليومية",settlements:"التسويات",shipping:"الشحن والتحصيل",inventory:"المخزون المحاسبي",reports:"التقارير"};
function Review(){
 const [params]=useSearchParams();const page=params.get("page")||"reports";
 const state=params.get("state")||"available";const user={id:"fixture-owner",is_owner:true};
 const status={cutover:{safe_active:false},balance_visibility:{status:"unavailable",banks:null},review_count:2,tasks:[]};
 return <><div className="fixture-banner">بيانات اصطناعية للاختبار فقط · ليست بيانات الإنتاج · كل الكتابات مرفوضة في بيئة العرض</div><nav className="fixture-nav">{Object.entries(names).map(([id,name])=><a key={id} href={"?page="+id}>{name}</a>)}<a href={"?page="+page+"&state=unavailable"}>غير جاهز</a><a href={"?page="+page+"&state=error"}>خطأ</a><a href={"?page="+page+"&state=empty"}>فارغ</a><a href={"?page="+page+"&state=loading"}>تحميل</a></nav><main key={page+state}><AccountingHeader page={{label:names[page]||names.reports}} /> <div className="mt-5">{page==="home"?<AccountingDailyWorkspace user={user} status={status} accountingPermissions={[]} />:page==="daily"?<AccountingDailyMovements />:page==="settlements"?<AccountingSettlementRegister />:page==="shipping"?<AccountingShippingCod />:page==="inventory"?<PartialWorkflowPage page={{id:"inventory-purchases",implementationStatus:"partial_existing_workflows"}} />:<AccountingReports initialDomain={params.get("report_view")||""}/>}</div></main></>;
}
createRoot(document.getElementById("root")).render(<BrowserRouter><Review /></BrowserRouter>);
