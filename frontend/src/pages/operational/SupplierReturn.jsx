import React, { useEffect, useRef, useState } from 'react';
import { operationalApi as api, messageFor, requestId } from './api';
import { Field, Notice } from './OpeningBalances';
import {visibleOperationalRead} from './visibleOperationalRead';
export default function SupplierReturn() {
 const [rows,setRows]=useState([]),[selected,setSelected]=useState(''),[amount,setAmount]=useState(''),[note,setNote]=useState(''),[file,setFile]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const identity=useRef(null),evidence=useRef(null),locked=useRef(false),readLane=useRef({});
 const [opened,setOpened]=useState(false),[loading,setLoading]=useState(false),[loadVersion,setLoadVersion]=useState(0),[readError,setReadError]=useState('');
 useEffect(()=>{
  if(!opened)return;
  return visibleOperationalRead(config=>api.reports(config),{
   onStart:()=>{setLoading(true);setReadError('');},
   onData:r=>{
    const facts=r.details?.facts||{};
    setRows(Object.entries(facts).filter(([key])=>key.startsWith('receipt:')).map(([key,fact])=>{
     const obligation=r.obligations.find(o=>(o.evidence_ids||[]).includes(key));
     const party=r.parties.find(p=>p.party_type==='supplier'&&p.party_id===obligation?.party_id);
     return{id:key.slice(8),label:`${party?.name||'فاتورة مورد'} · ${obligation?.business_date||''} · ${fact.amount} ${obligation?.currency||''}`};
    }));
   },
   onError:e=>setReadError(messageFor(e)),onEnd:()=>setLoading(false),
  },readLane.current);
 },[opened,loadVersion]);
 const changed=()=>{identity.current=null;setError('');setMessage('');};
 const save=async()=>{if(locked.current)return;if(!selected||!/^\d+(\.\d{1,2})?$/.test(amount)||Number(amount)<=0||!note.trim()||!file){setError('اختر فاتورة المورد وأدخل المبلغ والسبب وأرفق إثبات قبول المورد.');return;}locked.current=true;setBusy(true);identity.current ||= requestId();try{if(!evidence.current)evidence.current=await api.receipt(file);await api.supplierReturn({request_id:identity.current,receipt_id:selected,amount,note,evidence_receipt_id:evidence.current.id});setMessage('تم تسجيل قبول المورد للمرتجع');setSelected('');setAmount('');setNote('');identity.current=null;evidence.current=null;setFile(null);}catch(e){setError(messageFor(e));}finally{setBusy(false);locked.current=false;}};
 return <details className="op-card" onToggle={e=>setOpened(e.currentTarget.open)}><summary>مرتجع مقبول من المورد</summary><Notice error={error||readError} message={message}/><button type="button" disabled={busy||loading} onClick={()=>setLoadVersion(v=>v+1)}>تحديث فواتير المورد</button>{loading&&<p role="status">جارٍ قراءة فواتير المورد…</p>}<fieldset disabled={busy||loading}><Field label="فاتورة المورد"><select value={selected} onChange={e=>{setSelected(e.target.value);changed();}}><option value="">اختر فاتورة مورد صادرة</option>{rows.map(r=><option key={r.id} value={r.id}>{r.label}</option>)}</select></Field><Field label="قيمة المرتجع"><input inputMode="decimal" value={amount} onChange={e=>{setAmount(e.target.value);changed();}}/></Field><Field label="سبب المرتجع"><input value={note} onChange={e=>{setNote(e.target.value);changed();}}/></Field><Field label="إثبات قبول المورد"><input type="file" accept="image/jpeg,image/png,application/pdf" onChange={e=>{setFile(e.target.files[0]||null);evidence.current=null;changed();}}/></Field><button onClick={save}>حفظ المرتجع</button></fieldset></details>;
}
