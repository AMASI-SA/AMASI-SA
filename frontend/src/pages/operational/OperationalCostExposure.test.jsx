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
const render = async () => act(async () => root.render(<OperationalCostExposure />));

test('all old and new unreceived costs aggregate without presenting an assigned supplier', async () => {
  api.costExposure.mockResolvedValue({
    status: 'preview',
    engine_as_of: '2026-11-01T02:00:00Z', as_of: '2026-11-01T02:00:00Z',
    currencies: {SAR: {
      prestart_supplier_unreceived: '250.00',
      poststart_supplier_unreceived: '150.35',
      prestart_supplier_received_uninvoiced: '70.00',
      poststart_supplier_received_uninvoiced: '35.00',
      prestart_shipping_expected: '34.50',
      current_shipping_expected: '17.25',
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
  expect(host.textContent).toContain('250.00 SAR');
  expect(host.textContent).toContain('150.35 SAR');
  expect(host.textContent).toContain('قد يكون الإجمالي جزئيًا');
  expect(host.textContent).toContain('لا تُضاف تكاليف الطلبات القديمة تلقائيًا');
  expect(api.costExposure).toHaveBeenCalledTimes(1);
  expect(host.querySelectorAll('.op-cost-cards article')).toHaveLength(3);
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
});

test('disabled exposure does not retrieve any data or alter other reports', async () => {
  await act(async () => root.render(<OperationalCostExposure enabled={false}/>));
  expect(api.costExposure).not.toHaveBeenCalled();
  expect(host.textContent).toBe('');
});

test('load failure offers manual reload while preserving the rest of reporting', async () => {
  api.costExposure.mockRejectedValueOnce(new Error('unavailable'))
    .mockResolvedValue({status:'preview',currencies:{SAR:{prestart_supplier_unreceived:'40.00'}},
                        groups:[],issues:[]});
  await render();
  expect(host.textContent).toContain('تعذر تحميل التقرير');
  const button=host.querySelector('button');
  await act(async () => button.click());
  expect(host.textContent).toContain('40.00 SAR');
  expect(api.costExposure).toHaveBeenCalledTimes(2);
});
