import {inventoryConfiguration,inventoryOptionVisibility,pruneInventoryConfiguration,validateInventoryConfiguration,inventoryConfigDescription} from './inventoryProductConfiguration';

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
