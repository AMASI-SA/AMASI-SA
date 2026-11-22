// Each operation owns its durable key. Another tab must never overwrite or
// remove a receipt/payment whose result is still unknown.
export const inventoryCommandKey=(base,command)=>base+':command:'+encodeURIComponent(command.body.request_id);
export function readInventoryCommands(base,validate) {
  const keys=[];
  for(let index=0;index<localStorage.length;index++){
    const key=localStorage.key(index);
    if(key?.startsWith(base+':command:'))keys.push(key);
  }
  // Current command keys take precedence over the old single-operation key.
  keys.sort();keys.push(base);
  const result=[],seen=new Set();
  for(const key of keys){
    const text=localStorage.getItem(key);if(!text)continue;
    const command=JSON.parse(text);if(!command)continue;
    validate(command);
    const id=command.body.request_id;
    if(!seen.has(id)){result.push(command);seen.add(id);}
  }
  return result;
}
export function persistInventoryCommand(base,command) {
  localStorage.setItem(inventoryCommandKey(base,command),JSON.stringify(command));
}
export function removeInventoryCommand(base,command) {
  for(const key of [inventoryCommandKey(base,command),base]){
    const text=localStorage.getItem(key);if(!text)continue;
    const stored=JSON.parse(text);
    if(stored?.body?.request_id===command.body.request_id)localStorage.removeItem(key);
  }
}
