import {inventoryCommandKey,persistInventoryCommand,readInventoryCommands,removeInventoryCommand} from './inventoryCommandStorage';

const base='purchase:scope';
const command=(id,phase='invoice')=>({type:'purchase',phase,body:{request_id:id,expected_session_scope:'scope'}});
const valid=value=>{if(value.body?.expected_session_scope!=='scope'||!value.body?.request_id)throw Error('invalid');};
beforeEach(()=>localStorage.clear());
test('two tabs preserve their separate uncertain operations and only remove the confirmed one',()=>{
  const first=command('first'),second=command('second');
  persistInventoryCommand(base,first);persistInventoryCommand(base,second);
  expect(readInventoryCommands(base,valid)).toEqual([first,second]);
  removeInventoryCommand(base,first);
  expect(readInventoryCommands(base,valid)).toEqual([second]);
  expect(localStorage.getItem(inventoryCommandKey(base,second))).toBe(JSON.stringify(second));
});
test('a current checkpoint wins over the same legacy request while unrelated legacy recovery is retained',()=>{
  const legacy=command('first'),current=command('first','payment'),other=command('second');
  localStorage.setItem(base,JSON.stringify(legacy));persistInventoryCommand(base,current);
  expect(readInventoryCommands(base,valid)).toEqual([current]);
  localStorage.setItem(base,JSON.stringify(other));removeInventoryCommand(base,current);
  expect(readInventoryCommands(base,valid)).toEqual([other]);
});
test('cleanup of the matching legacy operation does not delete another tab current operation',()=>{
  const first=command('first'),second=command('second');
  localStorage.setItem(base,JSON.stringify(first));persistInventoryCommand(base,second);
  removeInventoryCommand(base,first);
  expect(localStorage.getItem(base)).toBeNull();expect(readInventoryCommands(base,valid)).toEqual([second]);
});
test('malformed saved operations fail closed and remain available for investigation',()=>{
  const key=base+':command:broken';localStorage.setItem(key,'{broken');
  expect(()=>readInventoryCommands(base,valid)).toThrow();expect(localStorage.getItem(key)).toBe('{broken');
});
test('other users drafts and operations are not inspected or removed',()=>{
  localStorage.setItem(base+':draft','{draft');localStorage.setItem('purchase:other:command:x','{other');
  expect(readInventoryCommands(base,valid)).toEqual([]);
  persistInventoryCommand(base,command('first'));removeInventoryCommand(base,command('first'));
  expect(localStorage.getItem(base+':draft')).toBe('{draft');expect(localStorage.getItem('purchase:other:command:x')).toBe('{other');
});
