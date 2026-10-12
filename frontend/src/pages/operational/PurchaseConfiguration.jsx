import React from 'react';
import {Field} from './OpeningBalances';
const choiceTypes=['select','radio','color','image'];
const textTypes=['text','string','textarea'];
export function purchaseConfigurationPayload(line){
  if(!line.purchase_configuration)return {};
  if(!line.location_id)throw Error('اختر موقع تسجيل الشراء.');
  return {location_id:line.location_id,purchase_configuration:line.purchase_configuration};
}
export function configurationDescription(line){
  const config=line.purchase_configuration;
  if(!config)return '';
  return [config.state==='raw'?'خام غير مخصص':'جاهز',...(line.purchase_option_labels||[]).map(s=>`${s.name}: ${s.value}`)].join(' · ');
}
export default function PurchaseConfiguration({line,index,locations,onChange}){
  const config=line.purchase_configuration;
  if(!config)return null;
  const suffix=`${index+1} — ${line.name}`;
  const setConfig=patch=>onChange('purchase_configuration',{...config,...patch});
  return <section aria-label={`مواصفات الشراء ${suffix}`}>
    <Field label={`حالة المنتج ${suffix}`}><select value={config.state} onChange={e=>setConfig({state:e.target.value,inputs:[]})}><option value="raw">خام غير مخصص</option><option value="ready">جاهز</option></select></Field>
    <Field label={`موقع تسجيل الشراء ${suffix}`}><select value={line.location_id||''} onChange={e=>onChange('location_id',e.target.value)}><option value="">اختر الموقع</option>{locations.map(l=><option key={l.id} value={l.id}>{l.name||l.code} {l.warehouse_name?`— ${l.warehouse_name}`:''}</option>)}</select></Field>
    <p>الموقع مرجع للشراء التشغيلي؛ لا يثبت إضافة كمية إلى المستودع.</p>
    {(line.options||[]).map(option=>{
      if(choiceTypes.includes(option.type))return <Field key={option.id} label={`${option.name} ${suffix}`}><select value={config.selections.find(s=>s.option_id===option.id)?.value_id||''} onChange={e=>setConfig({selections:[...config.selections.filter(s=>s.option_id!==option.id),...(e.target.value?[{option_id:option.id,value_id:e.target.value}]:[])]})}><option value="">{config.state==='raw'?'غير محدد — لا تتكرر الكمية على الخيارات':'اختر'}</option>{(option.values||[]).map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>;
      if(textTypes.includes(option.type)){
        if(config.state==='raw')return null;
        const Control=option.type==='textarea'?'textarea':'input';
        return <Field key={option.id} label={`${option.name}${option.required?' *':''} ${suffix}`}><Control maxLength={option.type==='textarea'?1000:100} value={config.inputs.find(s=>s.option_id===option.id)?.value||''} onChange={e=>setConfig({inputs:[...config.inputs.filter(s=>s.option_id!==option.id),...(e.target.value?[{option_id:option.id,value:e.target.value}]:[])]})}/></Field>;
      }
      return <p key={option.id} role="alert">{option.name}: هذا النوع من التخصيص غير مدعوم حاليًا؛ لا يمكن إثبات جاهزية المنتج به.</p>;
    })}
  </section>;
}
