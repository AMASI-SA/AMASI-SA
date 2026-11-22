import React from 'react';
import {Field} from './OpeningBalances';
import {InventoryProductOptions} from '../../components/inventory/suite/InventoryProductFields';
import {inventoryConfigDescription,validateInventoryConfiguration} from '../../lib/inventoryProductConfiguration';
export function purchaseConfigurationPayload(line,{allowMissingLocation=false}={}){
  if(!line.purchase_configuration)return {};
  if(!line.location_id&&!allowMissingLocation)throw Error('اختر موقع تسجيل الشراء.');
  validateInventoryConfiguration(line,line.purchase_configuration);
  return {...(line.location_id?{location_id:line.location_id}:{}),purchase_configuration:line.purchase_configuration};
}
export function configurationDescription(line){
  const config=line.purchase_configuration;
  if(!config)return '';
  return inventoryConfigDescription(line);
}
export default function PurchaseConfiguration({line,index,locations,onChange,locationOptional=false}){
  const config=line.purchase_configuration;
  if(!config)return null;
  const suffix=`${index+1} — ${line.name}`;
  return <section aria-label={`مواصفات الشراء ${suffix}`} className="mz-purchase-configuration">
    <InventoryProductOptions product={line} config={config} onChange={value=>onChange('purchase_configuration',value)} index={index} hideMode={line.kind==='component'}/>
    <Field label={`موقع تسجيل الشراء ${suffix}`}><select value={line.location_id||''} onChange={e=>onChange('location_id',e.target.value)}><option value="">{locationOptional?'يُحدد عند الاستلام':'اختر الموقع'}</option>{locations.map(l=><option key={l.id} value={l.id}>{l.name||l.code}{l.warehouse_name?' · '+l.warehouse_name:''}</option>)}</select></Field>
    <p className="mz-product-deferred">{locationOptional?'يمكن تحديد الموقع عند وصول البضاعة. إضافة الكمية للمخزون تتم عند تسجيل الاستلام.':'الموقع مرجع للشراء التشغيلي؛ لا يثبت إضافة كمية إلى المستودع.'}</p>
  </section>;
}
