import http from '../../lib/api';
import {operationalApi as api} from './api';
jest.mock('../../lib/api',()=>({get:jest.fn(),post:jest.fn()}));
beforeEach(()=>{jest.resetAllMocks();http.get.mockResolvedValue({data:{ok:true}});http.post.mockResolvedValue({data:{ok:true}});});
test('source and deliberate selection candidates are GET reads without writes',async()=>{
  await api.supplierInvoiceReview();await api.nativePurchaseCandidates('invoice/one');
  expect(http.get.mock.calls).toEqual([['/operational-balances/supplier-invoice-review'],['/operational-balances/native-purchase-links/invoice%2Fone/candidates']]);expect(http.post).not.toHaveBeenCalled();
});
test('preview and approval are distinct and preserve exact review proof/request',async()=>{
  const selection={invoice_id:'invoice',payment_links:[]}, approval={request_id:'stable',selection,expected_preview_hash:'proof'};
  await api.previewNativePurchaseLink(selection);await api.approveNativePurchaseLink(approval);
  expect(http.post.mock.calls).toEqual([['/operational-balances/native-purchase-links/preview',selection],['/operational-balances/native-purchase-links/approve',approval]]);
});
