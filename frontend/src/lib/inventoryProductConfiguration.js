export const inventoryChoiceTypes=['select','radio','color','image'];
export const inventoryTextTypes=['text','string','textarea'];

export function inventoryOptionVisibility(product,config){
  const fields=new Map((product?.options||[]).map(option=>[String(option.id),option]));
  const selected=new Map((config?.selections||[]).map(row=>[String(row.option_id),String(row.value_id)]));
  const deferred=new Set(config?.deferred_option_ids||[]),memo=new Map();
  function evaluate(id,active=new Set()){
    if(memo.has(id))return memo.get(id);
    if(!fields.has(id)||active.has(id)||active.size>=100)return 'pending';
    const next=new Set([...active,id]);
    const states=(fields.get(id).visibility_rules||[]).map(rule=>{
      const parent=String(rule.option_id),parentState=evaluate(parent,next);
      if(parentState==='hidden')return 'hidden';
      if(parentState==='pending'||deferred.has(parent)||!selected.has(parent))return 'pending';
      const match=selected.get(parent)===String(rule.value_id);
      return (rule.operator==='='?match:!match)?'visible':'hidden';
    });
    const result=states.includes('hidden')?'hidden':states.includes('pending')?'pending':'visible';
    memo.set(id,result);return result;
  }
  return Object.fromEntries([...fields.keys()].map(id=>[id,evaluate(id)]));
}

export function pruneInventoryConfiguration(product,config,changedOptionId=''){
  const result={...config,selections:(config.selections||[]).map(row=>({...row})),inputs:(config.inputs||[]).map(row=>({...row}))};
  if('deferred_option_ids' in config)result.deferred_option_ids=[...config.deferred_option_ids];
  const descendants=new Set();
  if(changedOptionId){
    let expanded=true;
    while(expanded){
      expanded=false;
      for(const option of product?.options||[]){
        if(!descendants.has(option.id)&&(option.visibility_rules||[]).some(rule=>rule.option_id===changedOptionId||descendants.has(rule.option_id))){descendants.add(option.id);expanded=true;}
      }
    }
  }
  const visibility=inventoryOptionVisibility(product,result);
  const keep=id=>visibility[id]==='visible'&&!descendants.has(id);
  result.selections=result.selections.filter(row=>keep(row.option_id));
  result.inputs=result.inputs.filter(row=>keep(row.option_id));
  if('deferred_option_ids' in result){result.deferred_option_ids=result.deferred_option_ids.filter(keep);result.state=result.deferred_option_ids.length?'raw':'ready';}
  return result;
}

export function inventoryConfiguration(product,variantId=''){
  const variant=product?.variants?.find(v=>String(v.variant_id)===String(variantId));
  const config={state:'raw',selections:(variant?.selections||[]).map(v=>({...v})),inputs:[]};
  if(product?.configuration_contract_version===2){config.deferred_option_ids=(product.options||[]).filter(option=>option.defer_allowed&&option.required).map(option=>option.id);return pruneInventoryConfiguration(product,config);}
  return config;
}
export function inventoryVariantFor(product,config,fallback=''){
  const variants=product?.variants||[];
  if(!variants.some(v=>v.selections?.length))return variants.find(v=>String(v.variant_id)===String(fallback))||null;
  const matches=variants.filter(v=>v.selections?.length&&v.selections.every(s=>config?.selections?.some(c=>String(c.option_id)===String(s.option_id)&&String(c.value_id)===String(s.value_id))));
  return matches.length===1?matches[0]:null;
}
export function validateInventoryConfiguration(product,config,variantId=''){
  if(!config)return;
  if(!['raw','ready'].includes(config.state)||!Array.isArray(config.selections)||!Array.isArray(config.inputs))throw Error('حدد حالة المنتج وخياراته.');
  if(product?.configuration_issues?.length)throw Error(product.configuration_issues.join(' · '));
  const options=product?.options||[];
  const explicit='deferred_option_ids' in config,deferred=explicit?config.deferred_option_ids:[];
  if(!Array.isArray(deferred)||deferred.length+config.selections.length+config.inputs.length>100||deferred.some(id=>typeof id!=='string')||new Set(deferred).size!==deferred.length)throw Error('خيارات التخصيص المؤجلة غير صالحة.');
  const seen=new Set();
  for(const selection of config.selections){
    const option=options.find(o=>String(o.id)===String(selection.option_id));
    if(!option||!inventoryChoiceTypes.includes(option.type)||!option.values?.some(v=>String(v.id)===String(selection.value_id))||seen.has(option.id))throw Error('أعد اختيار خيارات المنتج الحالية؛ توجد قيمة غير متاحة.');
    seen.add(option.id);
  }
  for(const input of config.inputs){
    const option=options.find(o=>String(o.id)===String(input.option_id));
    if(!option||!inventoryTextTypes.includes(option.type)||seen.has(option.id)||typeof input.value!=='string')throw Error('حقل التخصيص لا ينتمي لتعريف المنتج.');
    if(!input.value.trim()||Array.from(input.value).length>(option.type==='textarea'?1000:100)||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(input.value))throw Error(`راجع ${option.name}؛ القيمة فارغة أو غير صالحة أو طويلة.`);
    seen.add(option.id);
  }
  for(const id of deferred){const option=options.find(row=>row.id===id);if(!option?.defer_allowed||option.stock_identity||(product.physical_option_ids||[]).includes(id)||seen.has(id))throw Error('يمكن تأجيل التخصيص فقط؛ اللون والمقاس يحددان الآن.');}
  const visibility=inventoryOptionVisibility(product,config);
  if([...seen,...deferred].some(id=>visibility[id]!=='visible'))throw Error('أعد تحديد الخيارات التابعة؛ توجد قيمة لخيار مخفي.');
  if(!explicit&&config.state==='raw'&&config.inputs.length)throw Error('التخصيص حسب طلب العميل لا يحتفظ بنص جاهز.');
  if((explicit||config.state==='ready')&&options.some(o=>o.required&&visibility[o.id]==='visible'&&!seen.has(o.id)&&!deferred.includes(o.id)))throw Error('أكمل خيارات المنتج المطلوبة أو أجّل التخصيص المتاح.');
  if(explicit&&options.some(o=>o.stock_identity&&!seen.has(o.id)))throw Error('حدد اللون والمقاس الفعليين للمخزون.');
  if(product?.variants?.length){const variant=inventoryVariantFor(product,config,variantId);if(!variant||String(variant.variant_id)!==String(variantId))throw Error('حدد اللون والمقاس المطابقين لنسخة المنتج.');}
  const combinations=product?.selection_combinations||[],represented=new Set(combinations.flatMap(combination=>combination.map(row=>row.option_id)));
  const comparable=config.selections.filter(row=>represented.has(row.option_id));
  if(comparable.length&&!combinations.some(combination=>comparable.every(row=>combination.some(value=>value.option_id===row.option_id&&value.value_id===row.value_id))))throw Error('اللون والمقاس لا يطابقان تركيبة معتمدة في ميزان 2.');
  if(explicit&&combinations.length&&combinations.filter(combination=>combination.length===comparable.length&&comparable.every(row=>combination.some(value=>value.option_id===row.option_id&&value.value_id===row.value_id))).length!==1)throw Error('حدد تركيبة واحدة كاملة للون والمقاس.');
}
export function inventoryConfigurationLabels(product,config){
  return (product?.options||[]).flatMap(option=>{const selection=config?.selections?.find(row=>row.option_id===option.id),input=config?.inputs?.find(row=>row.option_id===option.id),value=selection?option.values?.find(row=>row.id===selection.value_id)?.name:input?.value;return value?[{option_id:option.id,name:option.name,value,...(selection?{value_id:selection.value_id}:{})}]:[];});
}
export function inventoryConfigDescription(line){
  const config=line.purchase_configuration;if(!config)return '';
  const fixed=(line.purchase_option_labels||inventoryConfigurationLabels(line,config)).map(v=>`${v.name}: ${v.value}`);
  const deferred=(config.deferred_option_ids||[]).map(id=>`${line.options?.find(option=>option.id===id)?.name||line.deferred_option_labels?.find(option=>option.option_id===id)?.name||'تخصيص'}: يُحدد عند طلب العميل`);
  return [config.state==='ready'?'جاهز بالمواصفات':'يُخصّص عند طلب العميل',...fixed,...deferred].join(' · ');
}
export function inventoryReceiptSpecifications(line){
  return Object.fromEntries((line.purchase_option_labels||[]).map(v=>[v.name,v.value]));
}
