/* Synthetic records only. No tenant ID, session cookie or provider request. */
const scope = 'a'.repeat(64);
const rules = [{option_id:'emb',value_id:'yes',operator:'='}];
const product = {id:'demo-fur',kind:'product',name:'فروة أطفال توضيحية',sku:'DEMO-FUR',unit:'piece',
  configuration_contract_version:2,configuration_issues:[],conditional_rules_verified:true,physical_option_ids:['size'],
  selection_combinations:[[{option_id:'size',value_id:'two'}]],options:[
    {id:'size',name:'المقاس',type:'radio',required:true,stock_identity:true,defer_allowed:false,values:[{id:'two',name:'سنتان'}]},
    {id:'emb',name:'تطريز الاسم',type:'radio',required:true,defer_allowed:true,values:[{id:'yes',name:'نعم'},{id:'no',name:'لا'}]},
    {id:'shape',name:'شكل التطريز',type:'radio',required:true,defer_allowed:true,visibility_rules:rules,values:[{id:'heart',name:'قلب'}]},
    {id:'name',name:'الاسم على الفروة',type:'text',required:true,defer_allowed:true,visibility_rules:rules,values:[]},
    {id:'note',name:'ملاحظة الصنف',type:'textarea',required:false,defer_allowed:true,values:[]},
    {id:'extra',name:'خيار إضافي اختياري',type:'radio',required:false,defer_allowed:false,values:[{id:'one',name:'واحد'}]},
  ]};
const necklace = {id:'demo-necklace',kind:'product',name:'سلسال توضيحي',sku:'DEMO-NECKLACE',unit:'piece',
  configuration_contract_version:2,configuration_issues:[],conditional_rules_verified:true,physical_option_ids:['color'],
  selection_combinations:[[{option_id:'color',value_id:'gold'}]],options:[
    {id:'color',name:'اللون',type:'radio',required:true,stock_identity:true,defer_allowed:false,values:[{id:'gold',name:'ذهبي'}]},
  ]};
const component = {id:'demo-thread',kind:'component',name:'خيط تطريز توضيحي',sku:'DEMO-THREAD',unit:'piece',options:[]};
// Required gift question/text start as preparation fields. The owner changes
// inventory roles; customer requiredness, choices and dependencies stay intact.
const sideProduct = {id:'demo-gift-product',kind:'product',name:'قطعة مع إضافات إهداء توضيحية',sku:'DEMO-GIFT',unit:'piece',
  configuration_contract_version:2,configuration_issues:[],conditional_rules_verified:true,
  inventory_option_policy_revision:0,physical_option_ids:['size-101'],
  selection_combinations:[[{option_id:'size-101',value_id:'medium-101'}]],options:[
    {id:'size-101',name:'مقاس القطعة',type:'radio',required:true,inventory_role:'physical',role_locked:true,
      stock_identity:true,defer_allowed:false,values:[{id:'medium-101',name:'متوسط'}]},
    {id:'gift-202',name:'هل تريد كرت إهداء',type:'radio',required:true,inventory_role:'customization',
      stock_identity:false,defer_allowed:true,values:[{id:'yes-202',name:'نعم'},{id:'no-202',name:'لا'}]},
    {id:'gift-text-303',name:'نص كرت الإهداء',type:'textarea',required:true,inventory_role:'customization',
      stock_identity:false,defer_allowed:true,visibility_rules:[{option_id:'gift-202',value_id:'yes-202',operator:'='}],values:[]},
    {id:'note-404',name:'ملاحظة التغليف',type:'text',required:false,inventory_role:'customization',
      stock_identity:false,defer_allowed:true,values:[]},
  ]};
const sidePolicyBefore = {product_id:sideProduct.id,revision:0,catalog_fingerprint:'b'.repeat(64),
  roles:{'size-101':'physical','gift-202':'customization','gift-text-303':'customization','note-404':'customization'},
  options:structuredClone(sideProduct.options),product:structuredClone(sideProduct)};
const sidePolicyAfter = {product_id:sideProduct.id,revision:1,catalog_fingerprint:sidePolicyBefore.catalog_fingerprint,
  roles:{'size-101':'physical','gift-202':'informational','gift-text-303':'informational','note-404':'informational'}};
sidePolicyAfter.product = {...structuredClone(sideProduct),inventory_option_policy_revision:1,
  options:sideProduct.options.map(option=>({...structuredClone(option),inventory_role:sidePolicyAfter.roles[option.id],defer_allowed:false}))};
sidePolicyAfter.options = structuredClone(sidePolicyAfter.product.options);
const locations = [{id:'demo-shelf',name:'A-01',warehouse_id:'demo-warehouse',warehouse_name:'المخزن التوضيحي'}];
const suppliers = [{id:'demo-supplier',name:'المورد التوضيحي',kind:'supplier',currency:'SAR'}];
const money = cents=>(cents/100).toFixed(2);
const cents = value=>{const [whole,fraction='']=String(value).split('.');return Number(whole)*100+Number(fraction.padEnd(2,'0'));};
function invoice(body) {
  const lines = body.lines.map((line,index)=>{
    const source = [product,necklace,component].find(item=>item.id===line.item_id);
    if (!source) throw Error('Unknown synthetic product');
    const config = line.purchase_configuration;
    const labels = [...(config?.selections || []).map(selected=>{
      const option = source.options.find(item=>item.id===selected.option_id);
      return {option_id:option.id,name:option.name,value:option.values.find(item=>item.id===selected.value_id).name};
    }),...(config?.inputs || []).map(input=>({option_id:input.option_id,name:source.options.find(item=>item.id===input.option_id).name,value:input.value}))];
    const net = line.quantity*cents(line.unit_price), tax = cents(line.tax);
    return {...structuredClone(source),...structuredClone(line),line_id:'demo-invoice-line-'+index,
      purchase_option_labels:labels,returned_quantity:0,remaining_quantity:line.quantity,
      received_quantity:0,pending_receipt_quantity:line.quantity,net:money(net),tax:money(tax),gross:money(net+tax),
      physical_stock_status:'not_received',personalizations:[]};
  });
  const net = lines.reduce((sum,line)=>sum+cents(line.net),0), tax = lines.reduce((sum,line)=>sum+cents(line.tax),0);
  return {id:'demo-invoice',obligation_id:'inventory-purchase:demo-invoice',supplier_id:body.supplier_id,
    supplier_name:suppliers[0].name,invoice_number:body.invoice_number,invoice_date:body.invoice_date,lines,
    net:money(net),tax:money(tax),gross:money(net+tax),settled:'0.00',outstanding:money(net+tax),currency:'SAR',
    receipt_tracking_version:1,receipt_status:'not_received',physical_stock_status:'not_received',can_receive:true,
    receipt_remaining_lines:lines.length,receipt_history:[]};
}
module.exports = {synthetic_only:true,scope,product,necklace,sideProduct,sidePolicyBefore,sidePolicyAfter,component,locations,suppliers,invoice,
  owner:{id:'demo-owner',role:'owner',is_owner:true,is_active:true,name:'مالك توضيحي'},
  context:{session_scope:scope,status:'active',can_manage_inventory_opening:true,permissions:{move:true,reports:true,view:true}},
};
