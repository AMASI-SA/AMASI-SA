import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import MovementHistory from './MovementHistory';
import {operationalApi as api} from './api';
jest.mock('./api', () => ({operationalApi:{movements:jest.fn()},messageFor:()=>'تعذر قراءة الحركات'}));
let root, host;
const row = {id:'internal-id', name:'مورد تجريبي', bank_name:'بنك تجريبي', amount:'50.00',currency:'SAR',direction:'outgoing',source:'mezan2',reference:'WEB-UAT-001',receipt_id:'private-receipt-id'};
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true; host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);jest.resetAllMocks();});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('reads saved movements and refreshes without creating or duplicating rows', async()=>{
  api.movements.mockResolvedValue({items:[row]});
  await act(async()=>root.render(<MovementHistory/>));
  expect(host.textContent).toContain('50.00 SAR');expect(host.textContent).toContain('مورد تجريبي');expect(host.textContent).toContain('مرفق');
  expect(host.textContent).not.toContain('internal-id');expect(host.textContent).not.toContain('private-receipt-id');
  await act(async()=>host.querySelector('button').click());
  expect(api.movements).toHaveBeenCalledTimes(2);expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
});
test('failed read is not an empty success and can be retried',async()=>{
  api.movements.mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce({items:[]});
  await act(async()=>root.render(<MovementHistory/>));expect(host.querySelector('[role=alert]')).not.toBeNull();expect(host.textContent).not.toContain('لا توجد حركات');
  await act(async()=>host.querySelector('button').click());expect(host.querySelector('[role=alert]')).toBeNull();expect(host.textContent).toContain('لا توجد حركات محفوظة بعد.');
});
test('late response from previous session cannot replace current session rows',async()=>{
  let resolveOld;api.movements.mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve;})).mockResolvedValueOnce({items:[{...row,name:'الجهة الحالية'}]});
  await act(async()=>root.render(<MovementHistory key="old"/>));
  await act(async()=>root.render(<MovementHistory key="new"/>));
  await act(async()=>resolveOld({items:[{...row,name:'جهة قديمة'}]}));
  expect(host.textContent).toContain('الجهة الحالية');expect(host.textContent).not.toContain('جهة قديمة');
});
