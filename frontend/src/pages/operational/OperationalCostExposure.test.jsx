import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import OperationalCostExposure from './OperationalCostExposure';
import {operationalApi as api} from './api';

jest.mock('./api', () => ({
  operationalApi: {costExposure: jest.fn()},
  messageFor: () => 'تعذر تحميل التقرير'
}));
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  jest.clearAllMocks();
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});
const render = async props => act(async () => root.render(<OperationalCostExposure {...props}/>));

test('all old and new unreceived costs aggregate without presenting an assigned supplier', async () => {
  api.costExposure.mockResolvedValue({
    status: 'preview', complete: false,
    engine_as_of: '2026-11-01T02:00:00Z', as_of: '2026-11-01T02:00:00Z',
    currencies: {SAR: {
      prestart_supplier_unreceived: '250.00',
      poststart_supplier_unreceived: '150.35',
      prestart_supplier_received_uninvoiced: '70.00',
      poststart_supplier_received_uninvoiced: '35.00',
      prestart_shipping_expected: '34.50',
      current_shipping_expected: '17.25'
    }},
    groups: [{period: 'prestart', kind: 'supplier', stage: 'under_fulfillment',
      party_name: 'منتجات لم تُستلم من المورد', amount: '250.00', currency: 'SAR',
      orders: 3, units: 6}],
    issues: [{code:'historical_supplier_cost_incomplete', count: 2}]
  });
  await render();
  expect(host.textContent).toContain('400.35 SAR');
  expect(host.textContent).toContain('105.00 SAR');
  expect(host.textContent).toContain('51.75 SAR');
  expect(host.textContent).toContain('557.10 SAR');
  expect(host.textContent).toContain('250.00 SAR');
  expect(host.textContent).toContain('150.35 SAR');
  expect(host.textContent).toContain('قد يكون الإجمالي جزئيًا');
  expect(host.textContent).toContain('لا تُضاف تكاليف الطلبات القديمة تلقائيًا');
  expect(api.costExposure).toHaveBeenCalledTimes(1);
  expect(host.querySelectorAll('.op-cost-cards article')).toHaveLength(4);
});

test('does not mix SAR and USD or invent a zero when historical costs are missing', async () => {
  api.costExposure.mockResolvedValue({status:'preview',currencies:{
    SAR:{prestart_supplier_unreceived:'100.00'},
    USD:{poststart_supplier_unreceived:'60.50'}
  },groups:[],issues:[{code:'historical_supplier_cost_incomplete',count:1}]});
  await render();
  const cards=[...host.querySelectorAll('.op-cost-currency')];
  expect(cards).toHaveLength(2);
  expect(cards[0].textContent).toContain('100.00 SAR');
  expect(cards[0].textContent).not.toContain('160.50');
  expect(cards[1].textContent).toContain('60.50 USD');
  expect(host.textContent).toContain('قد يكون الإجمالي جزئيًا');
  const sarCards=cards[0].querySelectorAll('article');
  expect(sarCards[1].querySelector('strong').textContent).toBe('غير مكتمل SAR');
  expect(sarCards[2].querySelector('strong').textContent).toBe('غير مكتمل SAR');
  expect(sarCards[3].querySelector('strong').textContent).toBe('غير مكتمل SAR');
});

test('disabled exposure does not retrieve any data or alter other reports', async () => {
  await render({enabled:false});
  expect(api.costExposure).not.toHaveBeenCalled();
  expect(host.textContent).toBe('');
});

test('load failure offers manual reload while preserving the rest of reporting', async () => {
  api.costExposure.mockRejectedValueOnce(new Error('unavailable'))
    .mockResolvedValue({status:'preview',currencies:{SAR:{prestart_supplier_unreceived:'40.00'}},
                        groups:[],issues:[]});
  await render();
  expect(host.textContent).toContain('تعذر تحميل التقرير');
  await act(async () => host.querySelector('button').click());
  expect(host.textContent).toContain('40.00 SAR');
  expect(api.costExposure).toHaveBeenCalledTimes(2);
});

test('current catalog estimates for old work are visibly marked as nonhistorical', async () => {
  api.costExposure.mockResolvedValue({status:'preview',currencies:{
    SAR:{prestart_supplier_unreceived:'33.50'}
  },groups:[],issues:[{code:'historical_current_catalog_estimate_used',count:1}]});
  await render();
  expect(host.textContent).toContain('33.50 SAR');
  expect(host.textContent).toContain('سعر التكلفة الحالي في كتالوج سلة');
  expect(host.textContent).toContain('ليس سعرًا تاريخيًا معتمدًا');
});

test('invalid monetary source is incomplete instead of silently replaced by zero', async () => {
  api.costExposure.mockResolvedValue({status:'preview',currencies:{
    SAR:{prestart_supplier_unreceived:'NaN',poststart_supplier_unreceived:'50.00'}
  },groups:[],issues:[]});
  await render();
  const card=host.querySelector('.op-cost-cards article strong');
  expect(card.textContent).toBe('غير مكتمل SAR');
  expect(card.textContent).not.toContain('50.00');
});

test('late read from previous actor scope is discarded', async () => {
  let finishOld;
  api.costExposure.mockImplementationOnce(() => new Promise(resolve => {finishOld=resolve;}))
    .mockResolvedValueOnce({status:'preview',currencies:{SAR:{prestart_supplier_unreceived:'20.00'}},groups:[],issues:[]});
  await render({scopeKey:'owner:first'});
  await render({scopeKey:'owner:second'});
  expect(host.textContent).toContain('20.00 SAR');
  await act(async () => finishOld({status:'preview',currencies:{SAR:{prestart_supplier_unreceived:'999.00'}},groups:[],issues:[]}));
  expect(host.textContent).not.toContain('999.00');
  expect(host.textContent).toContain('20.00 SAR');
});

test('frozen financial system does not claim a live quote is a frozen balance', async () => {
  api.costExposure.mockResolvedValue({status:'frozen',currencies:{},groups:[],issues:[]});
  await render();
  expect(host.textContent).toContain('النظام مجمد');
  expect(host.querySelectorAll('.op-cost-cards')).toHaveLength(0);
});

test('shipping gaps do not replace complete product sections with unknown amounts', async () => {
  api.costExposure.mockResolvedValue({status:'preview',complete:false,
    product_cost_currency:'SAR',component_complete:{unreceived_products:true,received_products:true,shipping:false},
    currencies:{SAR:{prestart_supplier_unreceived:'154.00',poststart_supplier_unreceived:'46.00'}},groups:[],
    issues:[{code:'historical_shipping_unassigned',count:2}]});
  await render();
  const cards=host.querySelectorAll('.op-cost-cards article strong');
  expect(cards[0].textContent).toBe('200.00 SAR');
  expect(cards[1].textContent).toBe('0.00 SAR');
  expect(cards[2].textContent).toBe('غير مكتمل SAR');
  expect(cards[3].textContent).toBe('غير مكتمل SAR');
  expect(host.textContent).toContain('ليست بعملة دفع العميل');
  expect(host.textContent).toContain('انتظار المراجعة أو قيد التنفيذ');
});

test('product gaps do not erase an independently complete shipping zero', async () => {
  api.costExposure.mockResolvedValue({status:'preview',complete:false,
    component_complete:{unreceived_products:false,received_products:false,shipping:true},
    currencies:{SAR:{}},groups:[],issues:[{code:'historical_supplier_cost_incomplete',count:1}]});
  await render();
  const cards=host.querySelectorAll('.op-cost-cards article strong');
  expect(cards[0].textContent).toBe('غير مكتمل SAR');
  expect(cards[2].textContent).toBe('0.00 SAR');
});
