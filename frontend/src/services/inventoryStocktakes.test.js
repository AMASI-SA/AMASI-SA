import api from '../lib/api';
import {loadInventoryStocktakeContext,listInventoryStocktakes,getInventoryStocktake,createInventoryStocktake,saveInventoryStocktake,actOnInventoryStocktake,postInventoryStocktake,loadInventoryStocktakeMovements} from './inventoryStocktakes';
jest.mock('../lib/api',()=>({__esModule:true,default:{get:jest.fn(),post:jest.fn(),put:jest.fn()},formatApiErrorDetail:value=>value?.message||value}));
beforeEach(()=>{api.get.mockResolvedValue({data:{native:true}});api.post.mockResolvedValue({data:{native:true}});api.put.mockResolvedValue({data:{native:true}});});
afterEach(()=>jest.clearAllMocks());
test('readers use only authenticated native stocktake paths and forward cancellation configuration',async()=>{
  const config={signal:new AbortController().signal};expect(await loadInventoryStocktakeContext(config)).toEqual({native:true});await listInventoryStocktakes(config);await getInventoryStocktake('count/id',config);await loadInventoryStocktakeMovements(config);
  expect(api.get.mock.calls).toEqual([['/inventory-v2/stocktakes/context',config],['/inventory-v2/stocktakes',config],['/inventory-v2/stocktakes/count%2Fid',config],['/inventory-v2/stocktakes/movements',config]]);expect(api.post).not.toHaveBeenCalled();expect(api.put).not.toHaveBeenCalled();
});
test('count commands preserve exact caller bodies and server-issued posting identity without opening-stock APIs',async()=>{
  const create={request_id:'stocktake:real-request',title:'جرد',warehouse_ids:['native-wh'],location_ids:[],row_ids:[]};
  const save={request_id:'stocktake:save',expected_revision:2,title:'جرد',lines:[{id:'native-row',counted_quantity:'0',unit:'piece',reason:'تالف'}]};
  const action={request_id:'stocktake:review',expected_revision:3,action:'approve',reason:''};
  const post={operation_id:'server-operation',expected_revision:4};
  await createInventoryStocktake(create);await saveInventoryStocktake('native-count',save);await actOnInventoryStocktake('native-count',action);await postInventoryStocktake('native-count',post);
  expect(api.put.mock.calls).toEqual([['/inventory-v2/stocktakes/native-count',save]]);expect(api.post.mock.calls).toEqual([['/inventory-v2/stocktakes',create],['/inventory-v2/stocktakes/native-count/actions',action],['/inventory-v2/stocktakes/native-count/post',post]]);
  expect(api.post.mock.calls[2][1]).toBe(post);
});
