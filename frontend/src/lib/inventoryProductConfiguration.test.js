import {inventoryConfiguration,inventoryOptionVisibility,pruneInventoryConfiguration,validateInventoryConfiguration,inventoryConfigDescription,inventoryConfigurationLabels} from './inventoryProductConfiguration';

const parent={id:'emb',name:'هل تريد تطريز الاسم',type:'radio',required:true,defer_allowed:true,values:[{id:'yes',name:'نعم'},{id:'no',name:'لا'}]};
const rules=[{option_id:'emb',value_id:'yes',operator:'='}];
const product={configuration_contract_version:2,physical_option_ids:['size'],options:[
  {id:'size',name:'المقاس',type:'select',required:true,stock_identity:true,values:[{id:'two',name:'سنتان'}]},parent,
  {id:'shape',name:'شكل التطريز',type:'radio',required:true,defer_allowed:true,visibility_rules:rules,values:[{id:'heart',name:'قلب'}]},
  {id:'name',name:'الاسم على الفروة',type:'text',required:true,defer_allowed:true,visibility_rules:rules,values:[]},
  {id:'note',name:'ملاحظة',type:'textarea',required:false,defer_allowed:true,values:[]},
  {id:'optional',name:'خيار إضافي',type:'select',required:false,defer_allowed:false,values:[{id:'one',name:'واحد'}]},
]};
const fixed=()=>({state:'ready',selections:[{option_id:'size',value_id:'two'},{option_id:'emb',value_id:'yes'},{option_id:'shape',value_id:'heart'}],inputs:[{option_id:'name',value:'فاطمة'}],deferred_option_ids:[]});

const sideProduct={...product,options:[...product.options,
  {id:'card',name:'كرت الإهداء',type:'radio',required:true,inventory_role:'informational',defer_allowed:false,values:[{id:'yes',name:'نعم'},{id:'no',name:'لا'}]},
  {id:'card-text',name:'نص الكرت',type:'textarea',required:true,inventory_role:'informational',defer_allowed:false,values:[],visibility_rules:[{option_id:'card',value_id:'yes',operator:'='}]},
]};

test('customer side questions and text do not block stock or create deferral',()=>{
  expect(()=>validateInventoryConfiguration(sideProduct,fixed())).not.toThrow();
  const initial=inventoryConfiguration(sideProduct);
  expect(initial.deferred_option_ids).toEqual(['emb']);
  expect(inventoryOptionVisibility(sideProduct,{selections:[{option_id:'card',value_id:'yes'}]} )['card-text']).toBe('visible');
  expect(inventoryOptionVisibility(sideProduct,{selections:[{option_id:'card',value_id:'no'}]} )['card-text']).toBe('hidden');
});

test('explicit review prunes side answers and side deferral without changing other fixed values',()=>{
  const old=fixed();old.selections.push({option_id:'card',value_id:'yes'});old.inputs.push({option_id:'card-text',value:'رسالة'});
  expect(()=>validateInventoryConfiguration(sideProduct,old)).toThrow();
  expect(pruneInventoryConfiguration(sideProduct,old)).toEqual(fixed());
  expect(pruneInventoryConfiguration(sideProduct,{...fixed(),state:'raw',deferred_option_ids:['card']})).toEqual(fixed());
  expect(inventoryConfigurationLabels(sideProduct,old).map(row=>row.option_id)).not.toContain('card-text');
});

test('a side addition cannot conceal a real deferred engraving or missing physical option',()=>{
  const engraved={...fixed(),inputs:[],deferred_option_ids:['name','card']};
  const clean=pruneInventoryConfiguration(sideProduct,engraved);
  expect(clean.state).toBe('raw');expect(clean.deferred_option_ids).toEqual(['name']);
  expect(()=>validateInventoryConfiguration(sideProduct,clean)).not.toThrow();
  expect(()=>validateInventoryConfiguration(sideProduct,{...clean,selections:clean.selections.filter(row=>row.option_id!=='size')})).toThrow();
});

test('reclassifying deferred text as a physical specification exposes an empty required field',()=>{
  const definition={...product,options:product.options.map(option=>option.id==='name'?{...option,inventory_role:'physical',stock_identity:true,defer_allowed:false}:option)};
  const old={...fixed(),state:'raw',inputs:[],deferred_option_ids:['name']};
  const current=pruneInventoryConfiguration(definition,old);
  expect(current.deferred_option_ids).toEqual([]);
  expect(current.inputs).toEqual([]);
  expect(()=>validateInventoryConfiguration(definition,current)).toThrow();
  current.inputs=[{option_id:'name',value:'مواصفة مطبوعة على القطعة'}];
  expect(()=>validateInventoryConfiguration(definition,current)).not.toThrow();
});

test('new stock explicitly defers required customization but never an optional note or physical size',()=>{
  const config=inventoryConfiguration(product);
  expect(config.deferred_option_ids).toEqual(['emb']);
  expect(inventoryOptionVisibility(product,config).name).toBe('pending');
  expect(()=>validateInventoryConfiguration(product,config)).toThrow();
  config.selections=[{option_id:'size',value_id:'two'}];
  expect(()=>validateInventoryConfiguration(product,config)).not.toThrow();
});

test('changing parent to no removes name, shape and descendant deferral and never restores stale name',()=>{
  const old=fixed();
  const declined=pruneInventoryConfiguration(product,{...old,selections:old.selections.map(row=>row.option_id==='emb'?{...row,value_id:'no'}:row)},'emb');
  expect(declined.inputs).toEqual([]);
  expect(declined.selections).toEqual([{option_id:'size',value_id:'two'},{option_id:'emb',value_id:'no'}]);
  expect(declined.state).toBe('ready');
  expect(()=>validateInventoryConfiguration(product,declined)).not.toThrow();
  const yes=pruneInventoryConfiguration(product,{...declined,selections:declined.selections.map(row=>row.option_id==='emb'?{...row,value_id:'yes'}:row)},'emb');
  expect(yes.inputs).toEqual([]);
  expect(()=>validateInventoryConfiguration(product,yes)).toThrow();
});

test('one deferred field coexists with fixed choices and summary preserves both',()=>{
  const config={...fixed(),inputs:[],deferred_option_ids:['name']};
  const cleaned=pruneInventoryConfiguration(product,config);
  expect(cleaned.state).toBe('raw');
  expect(()=>validateInventoryConfiguration(product,cleaned)).not.toThrow();
  const summary=inventoryConfigDescription({...product,purchase_configuration:cleaned});
  expect(summary).toContain('شكل التطريز: قلب');
  expect(summary).toContain('الاسم على الفروة: يُحدد عند طلب العميل');
});

test('optional unrelated omission is ready and absence differs from fixed no',()=>{
  expect(pruneInventoryConfiguration(product,fixed()).state).toBe('ready');
  expect(()=>validateInventoryConfiguration(product,fixed())).not.toThrow();
  const omitted={...fixed(),selections:[{option_id:'size',value_id:'two'}],inputs:[]};
  expect(()=>validateInventoryConfiguration(product,omitted)).toThrow();
  const deferred={...omitted,deferred_option_ids:['emb']};
  expect(()=>validateInventoryConfiguration(product,deferred)).not.toThrow();
  expect(inventoryOptionVisibility(product,deferred).name).toBe('pending');
});

test.each(['size','optional','unknown'])('cannot defer unproved or physical option %s',id=>{
  const config=fixed();config.deferred_option_ids=[id];config.selections=config.selections.filter(row=>row.option_id!==id);
  expect(()=>validateInventoryConfiguration(product,config)).toThrow();
});

test('forged hidden values and catalog cycles cannot pass validation',()=>{
  const config=fixed();config.selections[1].value_id='no';
  expect(()=>validateInventoryConfiguration(product,config)).toThrow('خيار مخفي');
  expect(()=>validateInventoryConfiguration({...product,configuration_issues:['ترابط خيارات المنتج يحتوي دورة']},fixed())).toThrow('دورة');
});

test('new configured purchases require a unique complete physical combination',()=>{
  const combo=[{option_id:'size',value_id:'two'}];
  expect(()=>validateInventoryConfiguration({...product,selection_combinations:[combo]},fixed())).not.toThrow();
  expect(()=>validateInventoryConfiguration({...product,selection_combinations:[combo,combo]},fixed())).toThrow('تركيبة واحدة');
});

const conditionalPhysicalProduct={configuration_contract_version:2,physical_option_ids:['kind','length'],options:[
  {id:'kind',name:'نوع القطعة',type:'radio',required:true,stock_identity:true,values:[{id:'plain',name:'قطعة عادية'},{id:'chain',name:'سلسلة'}]},
  {id:'length',name:'طول السلسلة',type:'select',required:true,stock_identity:true,values:[{id:'long',name:'طويلة'}],visibility_rules:[{option_id:'kind',value_id:'chain',operator:'='}]},
],selection_combinations:[[{option_id:'kind',value_id:'plain'}],[{option_id:'kind',value_id:'chain'},{option_id:'length',value_id:'long'}]]};

test.each(['plain','chain'])('physical requirements follow visibility for complete %s variant',kind=>{
  const config={state:'ready',selections:[{option_id:'kind',value_id:kind},...(kind==='chain'?[{option_id:'length',value_id:'long'}]:[])],inputs:[],deferred_option_ids:[]};
  expect(inventoryOptionVisibility(conditionalPhysicalProduct,config).length).toBe(kind==='plain'?'hidden':'visible');
  expect(()=>validateInventoryConfiguration(conditionalPhysicalProduct,config)).not.toThrow();
});

test('hidden physical values and incomplete visible physical dimensions are rejected',()=>{
  const hidden={state:'ready',selections:[{option_id:'kind',value_id:'plain'},{option_id:'length',value_id:'long'}],inputs:[],deferred_option_ids:[]};
  expect(()=>validateInventoryConfiguration(conditionalPhysicalProduct,hidden)).toThrow('خيار مخفي');
  const missing={...hidden,selections:[{option_id:'kind',value_id:'chain'}]};
  expect(()=>validateInventoryConfiguration(conditionalPhysicalProduct,missing)).toThrow();
});

test('pending physical dimension requires a parent answer even without variant mappings',()=>{
  const definition={...conditionalPhysicalProduct,physical_option_ids:['length'],selection_combinations:[],options:conditionalPhysicalProduct.options.map(option=>option.id==='kind'?{...option,required:false,stock_identity:false}:option)};
  const config={state:'ready',selections:[],inputs:[],deferred_option_ids:[]};
  expect(inventoryOptionVisibility(definition,config).length).toBe('pending');
  expect(()=>validateInventoryConfiguration(definition,config)).toThrow('حدد اللون والمقاس');
});

test('not-equals does not interpret unanswered parent as no',()=>{
  const dependent={...product,options:product.options.map(option=>option.id==='name'?{...option,visibility_rules:[{option_id:'emb',value_id:'yes',operator:'!='}]}:option)};
  expect(inventoryOptionVisibility(dependent,{selections:[],inputs:[],deferred_option_ids:[]}).name).toBe('pending');
  expect(inventoryOptionVisibility(dependent,{selections:[{option_id:'emb',value_id:'no'}],inputs:[]}).name).toBe('visible');
});

test('legacy request shape and whole-product raw validation remain compatible',()=>{
  const legacy={options:product.options.map(({defer_allowed,stock_identity,...option})=>option)};
  expect(inventoryConfiguration(legacy)).toEqual({state:'raw',selections:[],inputs:[]});
  const body=fixed();delete body.deferred_option_ids;body.state='raw';
  expect(()=>validateInventoryConfiguration(legacy,body)).toThrow('لا يحتفظ بنص جاهز');
});
