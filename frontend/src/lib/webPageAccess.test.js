import { grantedPageAt, webPageSections } from "./webPageAccess";

const selected = (id,to,extra={}) => ({id,to,...extra});
const permitted = (grant,path) => grantedPageAt([grant],new URL(path,"https://mezan.invalid"))?.id;
const balances = "/operational-balances";

test.each([
    ["balances.home",balances,balances],
    ["balances.log",balances+"/daily-movements",balances+"?view=log"],
    ["balances.log",balances+"/daily-movements",balances+"/daily-movements?view=log"],
    ["balances.assets",balances+"?view=assets",balances+"?view=ledger&kind=provider&id=tabby"],
    ["balances.parties",balances+"?view=parties",balances+"/daily-movements?view=ledger&kind=supplier&id=123"],
    ["balances.review",balances+"?view=reviews",balances+"?view=issues&kind=courier&id=mobile"],
    ["balances.entry",balances+"?view=entry",balances+"?view=special-entry&kind=bank&id=123"],
    ["balances.statements",balances+"/provider-statements",balances+"/provider-statements#history"],
    ["home.dashboard","/dashboard-advanced","/dashboard-advanced?from=2026-11-01"],
    ["fulfillment.files","/fulfillment-v2?stage=reviewed&view=files","/fulfillment-v2?view=files&stage=reviewed"],
    ["marketing.tiktok","/ads-manager?provider=tiktok","/ads-manager?provider=tiktok"],
])("assigned %s opens its actual page %s", (id,to,path)=>expect(permitted(selected(id,to),path)).toBe(id));

test.each([
    ["balances.home",balances,balances+"?view=entry"],
    ["balances.log",balances+"/daily-movements",balances+"/daily-movements?tab=parties"],
    ["balances.assets",balances+"?view=assets",balances+"?view=ledger&kind=supplier&id=123"],
    ["balances.parties",balances+"?view=parties",balances+"?view=ledger&kind=bank&id=123"],
    ["balances.parties",balances+"?view=parties",balances+"?view=ledger&kind=unknown&id=123"],
    ["balances.assets",balances+"?view=assets",balances+"?view=assets&view=entry"],
    ["balances.home",balances,balances+"?view=__proto__"],
    ["fulfillment.files","/fulfillment-v2?stage=reviewed&view=files","/fulfillment-v2?stage=reviewed&view=products"],
    ["fulfillment.files","/fulfillment-v2?stage=reviewed&view=files","/fulfillment-v2?view=files&stage=reviewed&stage=assembly"],
    ["marketing.all","/ads-manager","/ads-manager?provider=tiktok"],
    ["orders.list","/orders-v2","/orders-v2/100"],
    ["balances.home",balances,balances+"/access"],
])("separate page or ambiguous URL is denied for %s: %s", (id,to,path)=>expect(permitted(selected(id,to),path)).toBeUndefined());
test("server can declare a reviewed exact alias and owner-only entries cannot authorize access",()=>{
    expect(permitted(selected("page","/other",{aliases:["/safe?workspace=review"]}),"/safe?workspace=review")).toBe("page");
    expect(permitted(selected("admin","/employees-v2?workspace=permissions",{owner_only:true}),"/employees-v2?workspace=permissions")).toBeUndefined();
});
test("section list includes new groups and preserves server Arabic names",()=>{
    const page=selected("new.report","/report",{section:"new",section_label:"التقارير الدورية"});
    expect(webPageSections([page])).toEqual([{id:"new",label:"التقارير الدورية",pages:[page]}]);
});
