import { grantedPageAt, webPageSections } from "./webPageAccess";

const selected = (id,to,extra={}) => ({id,to,...extra});
const permitted = (grant,path) => grantedPageAt([grant],new URL(path,"https://mezan.invalid"))?.id;
const balances = "/operational-balances";

test('inventory stock/report aliases retain the same read grant and never open entry or posting tabs',()=>{
    const grant=selected('products.inventory','/inventory-v2',{mode:'read'});
    expect(permitted(grant,'/inventory-v2?tab=stock')).toBe('products.inventory');
    expect(permitted(grant,'/inventory-v2?tab=reports')).toBe('products.inventory');
    for(const query of ['tab=opening','tab=stocktake','tab=adjustments','tab=purchase','tab=stock&tab=opening','tab=stock&workspace=warehouse','tab=reports&action=post'])expect(permitted(grant,'/inventory-v2?'+query)).toBeUndefined();
});

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
test.each(['stocktake','adjustments','movements'])('native accounting inventory page permits %s without inheriting the product reader',tab=>{
    const address='/inventory-v2?tab='+tab;
    expect(permitted(selected('products.inventory','/inventory-v2'),address)).toBeUndefined();
    const native=selected('accounting.inventory.view','/integrations-v2?workspace=financial&page=inventory-purchases',{mode:'read'});
    expect(permitted(native,address)).toBe('accounting.inventory.view');
    expect(permitted(native,address+'&tab=opening')).toBeUndefined();
    expect(permitted(native,address+'&action=post')).toBeUndefined();
    expect(permitted(native,'/inventory-v2?tab=opening')).toBeUndefined();
});

test.each(['tabby','tamara','salla'])('a supported %s statement filter retains the same assigned page and read/write mode',provider=>{
    for(const mode of ['read','write']) {
        const grant=selected('balances.statements',balances+'/provider-statements',{mode});
        for(const hash of ['','#history','#upload']) {
            const target=new URL(balances+'/provider-statements?provider='+provider+hash,'https://mezan.invalid');
            expect(grantedPageAt([grant],target)).toBe(grant);
        }
    }
});

test('a statement provider filter preserves the exact reviewed alias and fragment boundary',()=>{
    const grant=selected('balances.statements','/reviewed-statement-entry',{
        mode:'read',aliases:[balances+'/provider-statements#history'],
    });
    expect(grantedPageAt([grant],new URL(balances+'/provider-statements?provider=tabby#history','https://mezan.invalid'))).toBe(grant);
    expect(permitted(grant,balances+'/provider-statements?provider=tabby#upload')).toBeUndefined();
    expect(permitted(grant,balances+'/provider-statements?provider=tabby')).toBeUndefined();
});

test.each([
    'provider=', 'provider=paypal', 'provider=emkan', 'provider=TABBY', 'provider=__proto__',
    'provider=tabby&provider=tabby', 'provider=tabby&provider=tamara',
    ...['workspace','view','stage','tab','section','action','page','kind','id'].map(key=>'provider=tabby&'+key+'=other'),
])('statement filters reject unsupported, ambiguous or extra selectors: %s',query=>{
    const grant=selected('balances.statements',balances+'/provider-statements',{mode:'write'});
    expect(permitted(grant,balances+'/provider-statements?'+query+'#history')).toBeUndefined();
});

test('a statement filter cannot borrow accounting, asset, report or owner-only page authority',()=>{
    const target=new URL(balances+'/provider-statements?provider=tabby#history','https://mezan.invalid');
    for(const id of ['accounting.inventory.view','balances.assets','balances.reports','unrelated.page']) {
        expect(grantedPageAt([selected(id,balances+'/provider-statements',{mode:'write'})],target)).toBeNull();
    }
    expect(grantedPageAt([],target)).toBeNull();
    expect(grantedPageAt([selected('balances.statements',balances+'/provider-statements',{owner_only:true,mode:'write'})],target)).toBeNull();
    const statement=selected('balances.statements',balances+'/provider-statements',{mode:'read'});
    expect(permitted(statement,'/integrations-v2?workspace=financial&provider=tabby')).toBeUndefined();
    expect(permitted(statement,balances+'/provider-fees?provider=tabby')).toBeUndefined();
    expect(permitted(statement,balances+'/provider-history?provider=tabby')).toBeUndefined();
});
