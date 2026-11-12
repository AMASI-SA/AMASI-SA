import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import CustomerReturns from './CustomerReturns';
import CustomerExchanges from './CustomerExchanges';
import {operationalApi as api} from './api';

jest.mock('./api',()=>({operationalApi:{context:jest.fn(),customerReturns:jest.fn(),customerExchanges:jest.fn(),
  movements:jest.fn(),entities:jest.fn(),returnOrder:jest.fn(),exchangeOrder:jest.fn(),
  saveCustomerReturn:jest.fn(),saveExchange:jest.fn(),returnShippingQuote:jest.fn()},
  requestId:jest.fn(),messageFor:()=> 'تعذرت القراءة'}));

let host,root;
const scope='a'.repeat(64);
beforeEach(()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();jest.resetAllMocks();
  Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
  host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
  api.context.mockResolvedValue({session_scope:scope,status:'active',permissions:{move:true}});
  for(const method of ['customerReturns','customerExchanges','movements','entities']) api[method].mockResolvedValue({items:[]});
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();delete document.visibilityState;});
const visibility=async value=>act(async()=>{
  Object.defineProperty(document,'visibilityState',{configurable:true,value});
  document.dispatchEvent(new Event('visibilitychange'));
});

describe.each([
  ['returns',CustomerReturns,'customerReturns'],['exchanges',CustomerExchanges,'customerExchanges'],
])('%s visible read lifetime',(_,Page,history)=>{
  test('hidden mount has no reads and completed reads never retry on visibility or focus',async()=>{
    Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
    await act(async()=>root.render(<Page/>));
    expect(api.context).not.toHaveBeenCalled();expect(api[history]).not.toHaveBeenCalled();
    await visibility('visible');
    expect(api.context).toHaveBeenCalledTimes(1);expect(api[history]).toHaveBeenCalledTimes(1);
    await visibility('hidden');await visibility('visible');
    await act(async()=>window.dispatchEvent(new Event('focus')));
    expect(api.context).toHaveBeenCalledTimes(1);expect(api[history]).toHaveBeenCalledTimes(1);
    expect(api.saveCustomerReturn).not.toHaveBeenCalled();expect(api.saveExchange).not.toHaveBeenCalled();
  });

  test('hiding the context read aborts it and a late result cannot launch child reads',async()=>{
    let resolve;
    api.context.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
    await act(async()=>root.render(<Page/>));
    const config=api.context.mock.calls[0][0];
    expect(config.signal.aborted).toBe(false);
    await visibility('hidden');expect(config.signal.aborted).toBe(true);
    await act(async()=>resolve({session_scope:scope,status:'active',permissions:{move:true}}));
    expect(api[history]).not.toHaveBeenCalled();expect(api.entities).not.toHaveBeenCalled();
  });

  test('hiding shared history reads aborts every request and only unfinished work resumes',async()=>{
    let resolve;
    api[history].mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
    await act(async()=>root.render(<Page/>));
    const config=api[history].mock.calls[0][0];
    expect(api.entities.mock.calls.every(call=>call[1].signal===config.signal)).toBe(true);
    if(history==='customerExchanges')expect(api.movements.mock.calls[0][0].signal).toBe(config.signal);
    await visibility('hidden');expect(config.signal.aborted).toBe(true);
    await act(async()=>resolve({items:[{id:'OLD PRIVATE'}]}));
    expect(host.textContent).not.toContain('OLD PRIVATE');
    expect(api[history]).toHaveBeenCalledTimes(1);
    await visibility('visible');
    expect(api[history]).toHaveBeenCalledTimes(2);expect(api.context).toHaveBeenCalledTimes(1);
    await visibility('hidden');await visibility('visible');
    expect(api[history]).toHaveBeenCalledTimes(2);
  });

  test('unmount aborts a pending history transport',async()=>{
    let resolve;
    api[history].mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
    await act(async()=>root.render(<Page/>));
    const config=api[history].mock.calls[0][0];
    await act(async()=>root.render(null));expect(config.signal.aborted).toBe(true);
    await act(async()=>resolve({items:[]}));
    expect(api[history]).toHaveBeenCalledTimes(1);
  });
});
