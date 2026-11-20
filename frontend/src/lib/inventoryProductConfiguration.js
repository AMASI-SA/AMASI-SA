export const inventoryChoiceTypes=['select','radio','color','image'];
export const inventoryTextTypes=['text','string','textarea'];
export function inventoryConfiguration(product,variantId=''){
  const variant=product?.variants?.find(v=>String(v.variant_id)===String(variantId));
  return {state:'raw',selections:(variant?.selections||[]).map(v=>({...v})),inputs:[]};
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
  const seen=new Set();
  for(const selection of config.selections){
    const option=options.find(o=>String(o.id)===String(selection.option_id));
    if(!option||!inventoryChoiceTypes.includes(option.type)||!option.values?.some(v=>String(v.id)===String(selection.value_id))||seen.has(option.id))throw Error('أعد اختيار خيارات المنتج الحالية؛ توجد قيمة غير متاحة.');
    seen.add(option.id);
  }
  for(const input of config.inputs){
    const option=options.find(o=>String(o.id)===String(input.option_id));
    if(!option||!inventoryTextTypes.includes(option.type)||seen.has(option.id)||typeof input.value!=='string')throw Error('حقل التخصيص لا ينتمي لتعريف المنتج.');
    if(!input.value.trim()||input.value.length>(option.type==='textarea'?1000:100))throw Error(`راجع ${option.name}؛ القيمة فارغة أو طويلة.`);
    seen.add(option.id);
  }
  if(config.state==='raw'&&config.inputs.length)throw Error('التخصيص حسب طلب العميل لا يحتفظ بنص جاهز.');
  if(config.state==='ready'&&options.some(o=>o.required&&!seen.has(o.id)))throw Error('أكمل خيارات المنتج الجاهز المطلوبة أو اختر التخصيص عند طلب العميل.');
  if(product?.variants?.length){const variant=inventoryVariantFor(product,config,variantId);if(!variant||String(variant.variant_id)!==String(variantId))throw Error('حدد اللون والمقاس المطابقين لنسخة المنتج.');}
}
export function inventoryConfigDescription(line){
  const config=line.purchase_configuration;if(!config)return '';
  return [config.state==='ready'?'جاهز بالمواصفات':'يُخصّص عند طلب العميل',...(line.purchase_option_labels||[]).map(v=>`${v.name}: ${v.value}`)].join(' · ');
}
export function inventoryReceiptSpecifications(line){
  return Object.fromEntries((line.purchase_option_labels||[]).map(v=>[v.name,v.value]));
}
