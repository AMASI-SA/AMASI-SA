import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import AdAccountSelection from './AdAccountSelection';
import {operationalApi as api} from './api';
global.IS_REACT_ACT_ENVIRONMENT = true;
jest.mock('./api', () => ({operationalApi: {adAccountSelection: jest.fn(), saveAdAccountSelection: jest.fn()}, requestId: () => 'selection-request', messageFor: () => 'تعذر الحفظ'}));
let host, root;
beforeEach(() => {
  jest.clearAllMocks();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  api.adAccountSelection.mockResolvedValue({version: 2, items: [
    {id:'snap', provider:'snapchat_ads', name:'Snap selected', external_account_id:'1', selected:true, editable:false},
    {id:'google', provider:'google_ads', name:'Google choice', external_account_id:'2', selected:false, editable:true}
  ]});
});
afterEach(() => {act(() => root.unmount()); host.remove();});
test('only explicit save submits editable selection and preserves native selection', async () => {
  const onSaved = jest.fn();
  api.saveAdAccountSelection.mockResolvedValue({version:3});
  await act(async () => root.render(<AdAccountSelection onSaved={onSaved} onClose={() => {}}/>));
  const inputs = host.querySelectorAll('input');
  expect(inputs[0].checked).toBe(true); expect(inputs[0].disabled).toBe(true);
  expect(api.saveAdAccountSelection).not.toHaveBeenCalled();
  await act(async () => inputs[1].click());
  expect(api.saveAdAccountSelection).not.toHaveBeenCalled();
  await act(async () => host.querySelector('button').click());
  expect(api.saveAdAccountSelection).toHaveBeenCalledWith({request_id:'selection-request', version:2, account_ids:['google']});
  expect(onSaved).toHaveBeenCalledTimes(1);
});
test('closing does not save and a failed load cannot submit', async () => {
  api.adAccountSelection.mockRejectedValueOnce(new Error('unavailable'));
  const close = jest.fn();
  await act(async () => root.render(<AdAccountSelection onSaved={() => {}} onClose={close}/>));
  expect(host.querySelector('button').disabled).toBe(true);
  await act(async () => host.querySelectorAll('button')[1].click());
  expect(close).toHaveBeenCalledTimes(1); expect(api.saveAdAccountSelection).not.toHaveBeenCalled();
});
