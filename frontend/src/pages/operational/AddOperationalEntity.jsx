import React, { useRef, useState } from 'react';
import { operationalApi as api, requestId, messageFor } from './api';
import { Field, Notice } from './OpeningBalances';

export default function AddOperationalEntity({kind,onAdded,onCancel}) {
  const [name,setName]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const request=useRef(null),locked=useRef(false);
  const save=async()=>{
    if(locked.current)return;
    if(name.trim().length<2){setError('أدخل اسمًا واضحًا للجهة.');return;}
    locked.current=true;setBusy(true);setError('');request.current ||= requestId();
    try {const row=await api.addEntity(kind,{request_id:request.current,name:name.trim(),currency:'SAR'});onAdded(row);}
    catch(e){setError(messageFor(e));}
    finally{locked.current=false;setBusy(false);}
  };
  return <div className="op-add"><Notice error={error}/><fieldset disabled={busy}><Field label="الاسم"><input value={name} onChange={e=>{setName(e.target.value);request.current=null;setError('');}}/></Field><p>العملة: SAR</p><button type="button" onClick={save}>إضافة</button><button type="button" onClick={onCancel}>إلغاء</button></fieldset></div>;
}
