import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ApprovedReports from './ApprovedReports';
import { operationalApi } from './api';

jest.mock('react-router-dom',()=>{global.TextEncoder=require('util').TextEncoder;return jest.requireActual('react-router');});
jest.mock('./api',()=>({operationalApi:{financialPosition:jest.fn(),refreshBalances:jest.fn()}}));
const context={actor:{is_owner:true},status:'active',session_scope:'owner-scope',permission_version:1};
const moneyColumn={key:'amount',label:'المبلغ',type:'money'};
const sourceTable=(overrides={})=>({id:'cod_expected',title:'تحصيل متوقع من العملاء',kind:'forecast',complete:false,
  columns:[{key:'name',label:'الجهة',type:'text'},moneyColumn],rows:[{id:'a',name:'شركة شحن',amount:'250.25'}],
  totals:{amount:null},notes:['هذه مبالغ متوقعة فقط.'],...overrides});
function snapshot(view='summary',overrides={}) {
  return {schema_version:1,authority:'operational_balance_report',view,status:'active',revision:7,
    session_scope:'owner-scope',started_at:'2026-11-01T00:00:00+03:00',as_of:'2026-11-21T18:00:00+03:00',
    permissions:{reports:true},currency_order:['SAR','USD'],forecast_pending:view==='summary',
    currencies:{SAR:{metrics:{liquidity:'1234.56',monetary_position:null,known_monetary_position:'1250.11',operating_profit:'450.25',net_profit:null},
      debt_totals:{receivable:'21.55',payable:null},known_debt_totals:{receivable:'21.55',payable:'6.00'},completeness:{actual:false,profit:false},
      current_rows:[{id:'liquidity',label:'النقد المتاح في البنوك والصناديق',amount:'1234.56'},{id:'debts_payable',label:'الالتزامات المسجلة علينا',amount:null,known_subtotal:'6.00'}],
      debt_rows:[{id:'providers',name:'منصات الدفع',receivable:'21.55',payable:'0.00'}],
      profit_rows:[{id:'net_sales',label:'إجمالي مبيعات الفترة حسب الطلبات',amount:'3000.00',complete:true,coverage:{orders:14,financial_eligible_orders:10,excluded_unpaid_orders:3,review_orders:1}},
        {id:'shipping',label:'تكلفة الشحن',amount:null,known_subtotal:'17.45',complete:false,reason:'أجرة ناقصة.'},
        {id:'net_profit',label:'صافي الربح',amount:null,complete:false,reason:'تكلفة التمويل والضريبة غير مثبتة.'}],
      forecast_rows:[{id:'cod_expected',label:'تحصيل محتمل',amount:null,known_subtotal:'250.25',complete:false}],tables:view==='summary'?[]:[sourceTable()],notes:[]},
      USD:{metrics:{liquidity:'50.00',monetary_position:'50.00'},debt_totals:{receivable:'0.00',payable:'0.00'},debt_rows:[],current_rows:[],profit_rows:[],tables:[],completeness:{actual:true}}},
    issues:[],dependencies:[],...overrides};
}
let host,root;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
  Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
  operationalApi.financialPosition.mockReset().mockImplementation(config=>Promise.resolve(snapshot(config.params.view)));
  operationalApi.refreshBalances.mockReset();
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const show=(props={},path='/operational-balances?view=reports')=>act(async()=>root.render(<MemoryRouter initialEntries={[path]}><div className="mz-finance"><ApprovedReports context={context} identity="owner" {...props}/></div></MemoryRouter>));
const button=label=>[...host.querySelectorAll('button')].find(el=>el.textContent===label);
const tab=label=>act(async()=>[...host.querySelectorAll('.report-tabs button')].find(el=>el.textContent===label).click());

it('reads one real saved summary and preserves unknown/partial totals without summing currencies or refreshing sources',async()=>{
  await show();
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
  expect(operationalApi.financialPosition.mock.calls[0][0].params).toEqual({view:'summary'});
  const metrics=[...host.querySelectorAll('.report-metric')];
  expect(metrics[0].textContent).toContain('1,234.56');expect(metrics[0].textContent).toContain('رصيد الشحن المقدم ليس نقدًا حرًا');
  expect(metrics[2].textContent).toContain('غير متاح');expect(metrics[2].textContent).toContain('الجزء المعلوم فقط: 6.00');
  expect(metrics[1].textContent).toContain('الجزء المسجل؛ التغطية غير مكتملة');
  expect(metrics[3].textContent).toContain('الجزء المعلوم فقط: 1,250.11');
  const select=host.querySelector('select');await act(async()=>{select.value='USD';select.dispatchEvent(new Event('change',{bubbles:true}));});
  expect(host.querySelector('.report-metric').textContent).toContain('50.00 USD');
  expect(host.textContent).not.toContain('1,234.56');
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('عرفات');expect(host.textContent).not.toContain('DEMO');
});

it('shows real profit components and excluded unpaid counts without turning missing tax/financing into zero',async()=>{
  await show({},'/operational-balances?view=reports&report_tab=profit');
  expect(host.textContent).toContain('450.25');expect(host.textContent).toContain('3,000.00');
  expect(host.textContent).toContain('الجزء المعلوم فقط: 17.45');
  expect(host.querySelector('.report-featured').textContent).toContain('غير متاح');
  expect(host.textContent).toContain('تكلفة التمويل والضريبة غير مثبتة.');
  expect(host.querySelector('.report-counts').textContent).toContain('مستبعدة لعدم الدفع3');
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
});

it('binds each detail view to the initial snapshot and keeps potential COD separate from current money',async()=>{
  await show();await tab('المبالغ والتكاليف المحتملة');
  expect(operationalApi.financialPosition).toHaveBeenLastCalledWith(expect.objectContaining({params:{view:'expected',expected_revision:7,
    expected_as_of:'2026-11-21T18:00:00+03:00',expected_started_at:'2026-11-01T00:00:00+03:00'}}));
  expect(host.textContent).toContain('250.25');expect(host.textContent).toContain('مبالغ محتملة؛ ليست نقدًا مستلمًا');
  expect(host.querySelector('.report-metrics')).toBeNull();
  expect(host.querySelector('[data-testid=report-table-cod_expected] tfoot').textContent).toContain('غير متاح');
  await tab('البنوك والصناديق');
  expect(operationalApi.financialPosition.mock.calls.map(([config])=>config.params.view)).toEqual(['summary','expected','accounts']);
  expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

it.each([{revision:8},{as_of:'2026-11-22T18:00:00+03:00'},{session_scope:'another-owner'},{started_at:'2026-10-01T00:00:00+03:00'},{view:'all'}])('rejects a detail snapshot mismatch %s',async change=>{
  operationalApi.financialPosition.mockImplementation(config=>Promise.resolve(snapshot(config.params.view,config.params.view==='summary'?{}:change)));
  await show();await tab('منصات الدفع والمرتجعات');
  expect(host.querySelector('[role=alert]')).not.toBeNull();expect(host.querySelector('[data-testid=report-table-cod_expected]')).toBeNull();
  expect(host.textContent).not.toContain('250.25');
});

it.each([{actor:{is_owner:false}},{status:'draft'},{status:'frozen'}])('never requests owner aggregates when context disallows reports %s',async change=>{
  await show({context:{...context,...change}});expect(operationalApi.financialPosition).not.toHaveBeenCalled();
  expect(host.querySelector('.report-metric')).toBeNull();
});

it('discards visible figures immediately across identity changes and ignores a late former owner response',async()=>{
  let resolve;operationalApi.financialPosition.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
  await show();await show({identity:'new-owner',context:{...context,session_scope:'new-scope'}});
  await act(async()=>resolve(snapshot()));
  expect(host.textContent).not.toContain('1,234.56');expect(host.querySelector('.report-metric')).toBeNull();
});

it('withholds a report when permission or snapshot identity is missing',async()=>{
  operationalApi.financialPosition.mockResolvedValue(snapshot('summary',{permissions:{reports:false}}));
  await show();expect(host.querySelector('[role=alert]')).not.toBeNull();expect(host.querySelector('.report-metric')).toBeNull();
});

it('does not certify Salla reconciliation, and does not sum a paginated refund list',async()=>{
  await show({},'/operational-balances?view=reports&report_tab=reconciliation');
  expect(host.textContent).toContain('لم تصدر شهادة مطابقة');
  expect(host.textContent).toContain('المقارنة الشاملة المستقلة بين سلة وميزان غير متاحة');
  expect(host.querySelector('a[href="/operational-balances/customer-returns"]')).not.toBeNull();
  expect(operationalApi.financialPosition.mock.calls.map(([config])=>config.params.view)).toEqual(['summary']);
});

it('labels partial provider totals and opens order reasons inline without leaving the report',async()=>{
  const provider=sourceTable({id:'providers',title:'منصات الدفع',kind:'actual',totals_basis:'known_subtotals',
    columns:[{key:'name',label:'المنصة',type:'text'},moneyColumn,{key:'pending_orders',label:'طلبات المراجعة',type:'exceptions'}],
    rows:[{id:'emkan',name:'إمكان',amount:'100.12',pending_orders:1,complete:false,pending_order_details:[{order_id:'one',order_number:'123',reasons:[{message:'دليل الدفع غير مكتمل'}]}]}],totals:{amount:'100.12',pending_orders:1}});
  operationalApi.financialPosition.mockImplementation(config=>{const value=snapshot(config.params.view);if(config.params.view==='providers')value.currencies.SAR.tables=[provider];return Promise.resolve(value);});
  await show();await tab('منصات الدفع والمرتجعات');
  expect(host.textContent).toContain('مجموع الجزء المعلوم');expect(host.textContent).toContain('ليست إجماليًا نهائيًا');
  expect(host.querySelector('.report-order-details').textContent).toContain('دليل الدفع غير مكتمل');
  expect(host.querySelector('.report-order-details a')).toBeNull();expect(host.textContent).toContain('ليست إجمالي جميع مرتجعات المتجر');
  expect(host.querySelector('tfoot .report-order-details')).toBeNull();
});

it('hides all report amounts after permission is revoked on a detail read',async()=>{
  operationalApi.financialPosition.mockImplementation(config=>config.params.view==='summary'?Promise.resolve(snapshot()):Promise.reject({response:{status:403}}));
  await show();await tab('البنوك والصناديق');
  expect(host.textContent).not.toContain('1,234.56');expect(host.querySelector('.report-scope')).toBeNull();
  expect(host.querySelector('[role=alert]')).not.toBeNull();
  await tab('الصورة الكاملة');
  expect(host.textContent).not.toContain('1,234.56');expect(host.querySelector('.report-metric')).toBeNull();
  await act(async()=>button('إعادة قراءة لقطة متسقة').click());
  expect(host.textContent).toContain('1,234.56');
});

it('retries a changed snapshot by rereading summary before the bound detail',async()=>{
  let attempt=0;operationalApi.financialPosition.mockImplementation(config=>{attempt++;if(attempt===2)return Promise.reject({response:{status:409}});return Promise.resolve(snapshot(config.params.view,{revision:attempt>2?8:7}));});
  await show();await tab('البنوك والصناديق');
  await act(async()=>button('إعادة قراءة لقطة متسقة').click());
  expect(operationalApi.financialPosition.mock.calls.map(([config])=>config.params.view)).toEqual(['summary','accounts','summary','accounts']);
  expect(operationalApi.financialPosition.mock.calls[3][0].params.expected_revision).toBe(8);
  expect(host.querySelector('[role=alert]')).toBeNull();
});

it('preserves exact large cent values and nulls in source tables',async()=>{
  operationalApi.financialPosition.mockImplementation(config=>{const value=snapshot(config.params.view);if(config.params.view!=='summary')value.currencies.SAR.tables=[sourceTable({rows:[{id:'big',name:'مبلغ موثق',amount:'9007199254740993.19'},{id:'unknown',name:'غير مثبت',amount:null}]})];return Promise.resolve(value);});
  await show();await tab('الموردون');
  const rows=host.querySelectorAll('tbody tr');expect(rows[0].textContent).toContain('9,007,199,254,740,993.19');
  expect(rows[1].textContent).toContain('غير متاح');expect(rows[1].textContent).not.toContain('0.00');
});

it('separates courier responsibility from customer collection and retained shipping fees',async()=>{
  operationalApi.financialPosition.mockImplementation(config=>{const value=snapshot(config.params.view);if(config.params.view==='couriers')value.currencies.SAR.deliveries={complete:false,notes:[],rows:[{
    id:'cod:1',order_number:'3001',party_type:'courier',party_id:'aramex',party_name:'أرامكس',payment_method:'cod',
    collection_status:'confirmed',collection_destination:'courier_receivable',collection_amount:'200.00',courier_responsibility:'200.00',
    shipping_confirmed:'24.50',shipping_settled:'0.00',shipping_outstanding:'24.50',collection_settled:'75.00',collection_outstanding:'125.00'}]};return Promise.resolve(value);});
  await show();await tab('الشحن والتحصيل');
  const shipping=host.querySelector('[data-testid=report-table-delivery_shipping]');
  const collections=host.querySelector('[data-testid=report-table-delivery_collections]');
  expect(shipping.textContent).toContain('24.50');expect(collections.textContent).toContain('ذمة شركة الشحن المثبتة');
  expect(collections.textContent).toContain('لم يُوثّق تحصيل الشركة من العميل');
  const cells=collections.querySelectorAll('tbody td');
  expect(cells[5].textContent).toContain('200.00');expect(cells[6].textContent).toContain('غير متاح');
  expect(collections.textContent).toContain('125.00');expect(collections.querySelector('tfoot')).toBeNull();
  expect(host.textContent).toContain('لا تنشأ مقاصة تلقائية دون تسوية مثبتة');
});

it('does not certify a pending driver collection amount as cash received',async()=>{
  operationalApi.financialPosition.mockImplementation(config=>{const value=snapshot(config.params.view);if(config.params.view==='drivers')value.currencies.SAR.deliveries={complete:false,notes:[],rows:[{
    id:'driver:1',order_number:'3002',party_type:'store_driver',party_id:'driver',party_name:'موصل المتجر',payment_method:'bank_transfer',
    collection_status:'pending_review',collection_destination:'store_bank_account_unknown',collection_amount:'315.20',store_collection:null}]};return Promise.resolve(value);});
  await show();await tab('الموصلون');
  const collections=host.querySelector('[data-testid=report-table-delivery_collections]');
  expect(collections.textContent).toContain('مبلغ التحصيل في المصدر');expect(collections.textContent).toContain('315.20');
  expect(collections.textContent).toContain('بانتظار اعتماد التحصيل');expect(collections.textContent).not.toContain('مبلغ تحصيل العميل الموثق');
  expect(collections.querySelectorAll('tbody td')[10].textContent).toContain('غير متاح');
});

it('keeps incomplete unidentified provider review cases visible in the same report',async()=>{
  operationalApi.financialPosition.mockImplementation(config=>{const value=snapshot(config.params.view);if(config.params.view==='providers')value.currencies.SAR.tables=[sourceTable({id:'providers',
    columns:[{key:'name',label:'المنصة',type:'text'},{key:'pending_orders',label:'طلبات المراجعة',type:'exceptions'}],
    rows:[{id:'salla',name:'سلة',pending_orders:3,pending_order_details:[]}],totals:{}})];return Promise.resolve(value);});
  await show();await tab('منصات الدفع والمرتجعات');
  const details=host.querySelector('.report-order-details');expect(details.querySelector('summary').textContent).toBe('فتح مراجعة الطلبات');
  expect(details.textContent).toContain('بعض طلبات المراجعة لا تتوفر لها تفاصيل');
  const before=operationalApi.financialPosition.mock.calls.length;
  await act(async()=>details.querySelector('summary').click());
  expect(operationalApi.financialPosition.mock.calls.length).toBe(before);
  expect(host.querySelector('.report-tabs [aria-current=page]').textContent).toBe('منصات الدفع والمرتجعات');
});

it.each([false,true])('exposes forecast currencies absent from the original summary, empty summary=%s',async empty=>{
  operationalApi.financialPosition.mockImplementation(config=>{const value=snapshot(config.params.view);
    if(config.params.view==='summary'){delete value.currencies.USD;if(empty)delete value.currencies.SAR;}
    else value.currencies.USD.tables=[sourceTable({rows:[{id:'usd',name:'تكلفة منتجات',amount:'125.55'}]})];
    return Promise.resolve(value);});
  await show();await tab('المبالغ والتكاليف المحتملة');
  const select=host.querySelector('select');expect([...select.options].map(option=>option.value)).toContain('USD');
  await act(async()=>{select.value='USD';select.dispatchEvent(new Event('change',{bubbles:true}));});
  expect(host.textContent).toContain('125.55 USD');
});
