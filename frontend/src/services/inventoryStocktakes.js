import api,{formatApiErrorDetail} from '../lib/api';

const base='/inventory-v2/stocktakes';
const value=response=>response.data;
const resource=id=>base+'/'+encodeURIComponent(String(id));

export const loadInventoryStocktakeContext=(config={})=>api.get(base+'/context',config).then(value);
export const listInventoryStocktakes=(config={})=>api.get(base,config).then(value);
export const getInventoryStocktake=(id,config={})=>api.get(resource(id),config).then(value);
export const createInventoryStocktake=body=>api.post(base,body).then(value);
export const saveInventoryStocktake=(id,body)=>api.put(resource(id),body).then(value);
export const actOnInventoryStocktake=(id,body)=>api.post(resource(id)+'/actions',body).then(value);
// operation_id comes from the approved server revision. Never generate one here.
export const postInventoryStocktake=(id,body)=>api.post(resource(id)+'/post',body).then(value);
export const loadInventoryStocktakeMovements=(config={})=>api.get(base+'/movements',config).then(value);

export function newStocktakeRequestId(){
  return 'stocktake:'+(globalThis.crypto?.randomUUID?.()||Date.now()+':'+Math.random().toString(16).slice(2));
}
export function inventoryStocktakeMessage(error){
  return formatApiErrorDetail(error?.response?.data?.detail)||'تعذر إتمام طلب الجرد. احتفظنا بالمدخلات؛ أعد المحاولة.';
}
