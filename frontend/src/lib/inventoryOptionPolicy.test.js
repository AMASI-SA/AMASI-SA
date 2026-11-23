import {applyInventoryOptionDefinition,inventoryOptionPolicyChanged} from './inventoryOptionPolicy';

const original = {
  id: 'product', item_id: 'product', kind: 'product', name: 'قطعة', draft_key: 'draft-1',
  quantity: '4', unit_price: '125.00', unit_cost: '100.00', tax: '75.00', location_id: 'shelf',
  configuration_contract_version: 2, inventory_option_policy_revision: 0,
  options: [{id:'size',name:'المقاس',type:'select',stock_identity:true,values:[{id:'large',name:'كبير'}]},
    {id:'note',name:'الملاحظة',type:'text',defer_allowed:true,values:[]}],
  purchase_configuration: {state:'raw',selections:[{option_id:'size',value_id:'large'}],inputs:[],deferred_option_ids:['note']},
};

test('applying the saved classification changes only configuration metadata and removes a side-option deferral', () => {
  const before = JSON.parse(JSON.stringify(original));
  const product = {...original, inventory_option_policy_revision: 1, options: original.options.map(option => option.id === 'note'
    ? {...option,inventory_role:'informational',defer_allowed:false} : option)};
  const updated = applyInventoryOptionDefinition(original,product);
  expect(updated.purchase_configuration).toEqual({state:'ready',selections:[{option_id:'size',value_id:'large'}],inputs:[],deferred_option_ids:[]});
  for (const key of ['quantity','unit_price','unit_cost','tax','location_id','draft_key']) expect(updated[key]).toBe(original[key]);
  expect(updated.inventory_option_policy_revision).toBe(1);
  expect(original).toEqual(before);
});

test('a new classification never invents a customer deferral or replaces a fixed customization', () => {
  const line = {...original, purchase_configuration:{state:'ready',selections:[{option_id:'size',value_id:'large'}],inputs:[{option_id:'note',value:'اسم مُجهّز'}]}};
  const product = {...original, inventory_option_policy_revision:1, options: original.options.map(option => option.id === 'note'
    ? {...option,inventory_role:'customization',defer_allowed:true} : option)};
  expect(applyInventoryOptionDefinition(line,product).purchase_configuration).toEqual({...line.purchase_configuration,deferred_option_ids:[]});
});

test('saved draft review is required only when a product policy revision changed', () => {
  expect(inventoryOptionPolicyChanged(original,{...original,inventory_option_policy_revision:1})).toBe(true);
  expect(inventoryOptionPolicyChanged(original,{...original})).toBe(false);
  expect(inventoryOptionPolicyChanged({...original,inventory_option_policy_revision:undefined},{...original})).toBe(false);
  expect(inventoryOptionPolicyChanged({...original,kind:'component'},{...original,inventory_option_policy_revision:1})).toBe(false);
});
