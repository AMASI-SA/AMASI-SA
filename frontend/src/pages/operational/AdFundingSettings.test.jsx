import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import AdFundingSettings from './AdFundingSettings';
import {operationalApi as api} from './api';
global.IS_REACT_ACT_ENVIRONMENT = true;
jest.mock('./api', () => ({operationalApi:{adFundingSettings:jest.fn(),saveAdFundingSettings:jest.fn()},requestId:()=> 'funding-request',messageFor:()=> 'تعذر الحفظ'}));
let host, root;
beforeEach(()=>{
  jest.clearAllMocks(); host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
  api.adFundingSettings.mockResolvedValue({status:'draft',items:[{integration_account_id:'snap',name:'Snap',currency:'USD',version:0,editable:true,configured:false}]});
});
afterEach(()=>{act(()=>root.unmount());host.remove();});
async function change(element,value) {
  await act(async()=>{Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element),'value').set.call(element,value);element.dispatchEvent(new Event('change',{bubbles:true}));element.dispatchEvent(new Event('input',{bubbles:true}));});
}
test('explicit approval preserves account currency and requires evidence',async()=>{
  const saved=jest.fn();api.saveAdFundingSettings.mockResolvedValue({version:1});
  await act(async()=>root.render(<AdFundingSettings onSaved={saved} onClose={()=>{}}/>));
  expect(api.saveAdFundingSettings).not.toHaveBeenCalled();
  await change(host.querySelector('select'),'snap');
  await act(async()=>host.querySelector('button').click());
  expect(api.saveAdFundingSettings).not.toHaveBeenCalled();
  await change(host.querySelector('textarea'),'اعتماد محفظة ودين');
  await act(async()=>host.querySelector('button').click());
  expect(api.saveAdFundingSettings).toHaveBeenCalledWith({request_id:'funding-request',integration_account_id:'snap',currency:'USD',version:0,mode:'wallet_first',evidence:'اعتماد محفظة ودين'});
  expect(saved).toHaveBeenCalledTimes(1);
});
test('active setup cannot save',async()=>{
  api.adFundingSettings.mockResolvedValueOnce({status:'active',items:[]});
  await act(async()=>root.render(<AdFundingSettings onSaved={()=>{}} onClose={()=>{}}/>));
  expect(host.querySelector('fieldset').disabled).toBe(true);
  expect(api.saveAdFundingSettings).not.toHaveBeenCalled();
});
test('save failure retains input and retry request identity',async()=>{
  api.saveAdFundingSettings.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({});
  await act(async()=>root.render(<AdFundingSettings onSaved={()=>{}} onClose={()=>{}}/>));
  await change(host.querySelector('select'),'snap');await change(host.querySelector('textarea'),'اعتماد المالك');
  await act(async()=>host.querySelector('button').click());
  expect(host.querySelector('textarea').value).toBe('اعتماد المالك');
  await act(async()=>host.querySelector('button').click());
  expect(api.saveAdFundingSettings.mock.calls[0][0]).toEqual(api.saveAdFundingSettings.mock.calls[1][0]);
});
