import React from 'react';
import {Field} from './OpeningBalances';
import {InventoryProductOptions} from '../../components/inventory/suite/InventoryProductFields';
import {validateInventoryConfiguration} from '../../lib/inventoryProductConfiguration';
export function purchaseConfigurationPayload(line){
  if(!line.purchase_configuration)return {};
  if(!line.location_id)throw Error('اختر موقع تسجيل الشراء.');
  validateInventoryConfiguration(line,line.purchase_configuration);
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
  return <section aria-label={`مواصفات الشراء ${suffix}`} className="mz-purchase-configuration">
    <InventoryProductOptions product={line} config={config} onChange={value=>onChange('purchase_configuration',value)} index={index} hideMode={line.kind==='component'}/>
    <Field label={`موقع تسجيل الشراء ${suffix}`}><select value={line.location_id||''} onChange={e=>onChange('location_id',e.target.value)}><option value="">اختر الموقع</option>{locations.map(l=><option key={l.id} value={l.id}>{l.name||l.code}{l.warehouse_name?' · '+l.warehouse_name:''}</option>)}</select></Field>
    <p className="mz-product-deferred">الموقع مرجع للشراء التشغيلي؛ لا يثبت إضافة كمية إلى المستودع.</p>
  </section>;
}
