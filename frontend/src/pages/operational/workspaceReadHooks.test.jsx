import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import http from '../../lib/api';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useWorkspaceRead } from './workspaceReadHooks';

jest.mock('../../lib/api',()=>({get:jest.fn(),delete:jest.fn()}));
global.IS_REACT_ACT_ENVIRONMENT=true;
const flush=async()=>{for(let n=0;n<16;n+=1)await Promise.resolve();};
function Page(){const read=useWorkspaceRead(config=>api.home(config),'same-query');return <output>{read.error||read.data?.result||'loading'}</output>;}

test('a delayed hidden-page DELETE cannot cancel a resumed read of the same query',async()=>{
  jest.useFakeTimers();
  const visibility=jest.spyOn(document,'visibilityState','get').mockReturnValue('visible');
  const hidden=jest.spyOn(document,'hidden','get').mockReturnValue(false);
  const online=jest.spyOn(navigator,'onLine','get').mockReturnValue(true);
  const leases=new Set();
  let finishOldDelete;
  http.delete.mockImplementationOnce((url,config)=>new Promise(resolve=>{
    finishOldDelete=()=>{leases.delete(config.params.consumer_id);resolve({data:{released:true}});};
  })).mockImplementation(async(url,config)=>{leases.delete(config.params.consumer_id);return {data:{released:true}};});
  http.get.mockImplementation(async(url,config)=>{
    const id=config.headers['X-Mezan-Read-Consumer'];
    if(config.headers['X-Mezan-Read-Poll']==='1'){
      if(!leases.has(id))throw new Error('Resumed lease was cancelled');
      return {status:200,data:{result:'ready'}};
    }
    leases.add(id);
    return {status:202,data:{status:'preparing',key:'same-result',retry_after_ms:500}};
  });
  const host=document.createElement('div');document.body.appendChild(host);const root=createRoot(host);
  try{
    await act(async()=>{root.render(<Page/>);await flush();});
    expect(http.get).toHaveBeenCalledTimes(1);
    const first=http.get.mock.calls[0][1];
    await act(async()=>{visibility.mockReturnValue('hidden');hidden.mockReturnValue(true);document.dispatchEvent(new Event('visibilitychange'));await flush();});
    expect(first.signal.aborted).toBe(true);expect(http.delete).toHaveBeenCalledTimes(1);
    await act(async()=>{visibility.mockReturnValue('visible');hidden.mockReturnValue(false);document.dispatchEvent(new Event('visibilitychange'));await flush();});
    expect(http.get).toHaveBeenCalledTimes(2);
    const second=http.get.mock.calls[1][1];
    expect(second.headers['X-Mezan-Read-Consumer']).not.toBe(first.headers['X-Mezan-Read-Consumer']);
    await act(async()=>{finishOldDelete();await flush();});
    expect(leases.has(second.headers['X-Mezan-Read-Consumer'])).toBe(true);
    await act(async()=>{jest.advanceTimersByTime(500);await flush();});
    expect(host.textContent).toBe('ready');expect(http.get).toHaveBeenCalledTimes(3);
    await act(async()=>{jest.advanceTimersByTime(60000);await flush();});
    expect(http.get).toHaveBeenCalledTimes(3);
  }finally{
    await act(async()=>root.unmount());host.remove();visibility.mockRestore();hidden.mockRestore();online.mockRestore();jest.useRealTimers();
  }
});
