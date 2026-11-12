import http from '../../lib/api';
import {operationalApi as api} from './api';
jest.mock('../../lib/api',()=>({get:jest.fn(),post:jest.fn()}));
beforeEach(()=>{jest.resetAllMocks();http.get.mockResolvedValue({data:{items:[]}});http.post.mockResolvedValue({data:{}});});

test.each([
  ['customerReturns',[],'/customer-returns'],['customerExchanges',[],'/customer-exchanges'],
  ['movements',[],'/movements'],['returnOrder',['order/one'],'/customer-returns/order/order%2Fone'],
  ['exchangeOrder',['order/one'],'/customer-exchanges/order/order%2Fone'],
  ['supplierInvoiceReview',[],'/supplier-invoice-review'],
  ['nativePurchaseCandidates',['invoice/one'],'/native-purchase-links/invoice%2Fone/candidates'],
])('%s forwards the read signal without changing its endpoint',async(method,args,path)=>{
  const config={signal:new AbortController().signal};await api[method](...args,config);
  expect(http.get).toHaveBeenCalledWith('/operational-balances'+path,config);expect(http.post).not.toHaveBeenCalled();
});
test('preview-only routes carry the signal and exact selected evidence',async()=>{
  const config={signal:new AbortController().signal};
  await api.returnShippingQuote('courier','carrier','123',config);
  expect(http.get).toHaveBeenLastCalledWith('/operational-balances/customer-returns/shipping-quote/courier/carrier',{...config,params:{order_number:'123'}});
  await api.receivingInvoiceCoverage(config);
  expect(http.get).toHaveBeenLastCalledWith('/operational-balances/supplier-invoice-review',{...config,params:{receiving_only:true}});
  await api.receivingCorrectionPreview('invoice','stamp',7,config);
  expect(http.get).toHaveBeenLastCalledWith('/operational-balances/supplier-invoice-review/receiving/invoice/correction-preview',{...config,params:{expected_as_of:'stamp',expected_revision:7}});
  const selection={invoice_id:'invoice'};await api.previewNativePurchaseLink(selection,config);
  expect(http.post).toHaveBeenLastCalledWith('/operational-balances/native-purchase-links/preview',selection,config);
});
test('explicit writes retain their payload and do not receive a read cancellation signal',async()=>{
  const body={request_id:'same-intent'};
  await api.saveCustomerReturn(body,'case');expect(http.post).toHaveBeenLastCalledWith('/operational-balances/customer-returns/case/confirm',body);
  await api.saveExchange(body,'case');expect(http.post).toHaveBeenLastCalledWith('/operational-balances/customer-exchanges/case/actions',body);
  await api.approveNativePurchaseLink(body);expect(http.post).toHaveBeenLastCalledWith('/operational-balances/native-purchase-links/approve',body);
});
