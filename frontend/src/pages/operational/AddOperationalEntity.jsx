import React, { useRef, useState } from 'react';
import { operationalApi as api, requestId, messageFor } from './api';
import { Field, Notice } from './OpeningBalances';

export default function AddOperationalEntity({kind,onAdded,onCancel,onBusyChange}) {
  const external = kind === 'external_person';
  const [name,setName]=useState(''),[phone,setPhone]=useState(''),[notes,setNotes]=useState('');
  const [personType,setPersonType]=useState('person'),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const request=useRef(null),locked=useRef(false);
  const invalidate=()=>{request.current=null;setError('');};
  const save=async()=>{
    if(locked.current)return;
    if(name.trim().length<2){setError('أدخل اسمًا واضحًا للجهة.');return;}
    locked.current=true;setBusy(true);onBusyChange?.(true);setError('');request.current ||= requestId();
    const body={request_id:request.current,name:name.trim(),currency:'SAR'};
    if(external)Object.assign(body,{phone:phone.trim(),notes:notes.trim(),person_type:personType});
    try {const row=await api.addEntity(kind,body);onAdded(row);}
    catch(e){
      const code=e?.response?.data?.detail?.code || e?.response?.data?.code;
      setError(code==='operational_external_name_duplicate'?'اسم الجهة الخارجية موجود مسبقًا. اختر الجهة من القائمة.':messageFor(e));
    }
    finally{locked.current=false;setBusy(false);onBusyChange?.(false);}
  };
  return <div className="op-add"><Notice error={error}/><fieldset disabled={busy}>
    <Field label={kind==='cash'?'اسم الصندوق':'الاسم'}><input value={name} onChange={e=>{setName(e.target.value);invalidate();}}/></Field>
    {external&&<>
      <Field label="التصنيف"><select value={personType} onChange={e=>{setPersonType(e.target.value);invalidate();}}><option value="person">شخص</option><option value="organization">شركة أو مؤسسة</option></select></Field>
      <Field label="رقم الجوال (اختياري)"><input type="tel" maxLength={40} value={phone} onChange={e=>{setPhone(e.target.value);invalidate();}}/></Field>
      <Field label="ملاحظات (اختياري)"><textarea maxLength={500} value={notes} onChange={e=>{setNotes(e.target.value);invalidate();}}/></Field>
    </>}
    <p>العملة · ريال سعودي</p><button type="button" onClick={save}>{busy?'جارٍ الحفظ…':'إضافة'}</button><button type="button" onClick={onCancel}>إلغاء</button>
  </fieldset></div>;
}
