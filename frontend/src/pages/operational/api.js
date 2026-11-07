import api from '../../lib/api';
const base = '/operational-balances';
const data = promise => promise.then(response => response.data);
export const operationalApi = {
  customerWalletContext: () => data(api.get(`${base}/customer-wallet/context`)),
  saveCustomerWalletSnapshot: body => data(api.post(`${base}/customer-wallet/snapshot`, body)),
  receivingInvoiceCoverage: () => data(api.get(`${base}/supplier-invoice-review`, {params: {receiving_only: true}})),
  receivingCorrectionPreview: (invoiceId, asOf, revision) => data(api.get(
    `${base}/supplier-invoice-review/receiving/${encodeURIComponent(invoiceId)}/correction-preview`,
    {params: {expected_as_of: asOf, expected_revision: revision}})),
  supplierInvoiceReview: () => data(api.get(`${base}/supplier-invoice-review`)),
  providerHistoryReviews: () => data(api.get(`${base}/provider-history-reviews`)),
  approveProviderHistory: body => data(api.post(`${base}/provider-history-reviews/approve`, body)),
  nativePurchaseCandidates: id => data(api.get(`${base}/native-purchase-links/${encodeURIComponent(id)}/candidates`)),
  previewNativePurchaseLink: body => data(api.post(`${base}/native-purchase-links/preview`, body)),
  approveNativePurchaseLink: body => data(api.post(`${base}/native-purchase-links/approve`, body)),
  salaryStartContext: () => data(api.get(`${base}/salary-start/context`)),
  previewSalaryStart: body => data(api.post(`${base}/salary-start/preview`, body)),
  approveSalaryStart: body => data(api.post(`${base}/salary-start/approve`, body)),
  adFundingSettings: () => data(api.get(`${base}/ad-funding-settings`)),
  saveAdFundingSettings: body => data(api.post(`${base}/ad-funding-settings`, body)),
  inventoryOpeningContext: () => data(api.get(`${base}/inventory-opening/context`)),
  previewInventoryOpening: body => data(api.post(`${base}/inventory-opening/preview`, body)),
  approveInventoryOpening: body => data(api.post(`${base}/inventory-opening/approve`, body)),
  adAccountSelection: () => data(api.get(`${base}/ad-account-selection`)),
  saveAdAccountSelection: body => data(api.post(`${base}/ad-account-selection`, body)),
  inventoryAvailability: () => data(api.get(`${base}/inventory`)),
  supplierAdjustmentEntry: (supplier,number) => data(api.get(`${base}/supplier-adjustments/entry/${encodeURIComponent(supplier)}/${encodeURIComponent(number)}`)),
  saveSupplierAdjustment: body => data(api.post(`${base}/supplier-adjustments`,body)),
  standaloneInventoryContext: () => data(api.get(`${base}/standalone-inventory/context`)),
  standaloneInventoryControl: body => data(api.post(`${base}/standalone-inventory/control`, body)),
  previewStandaloneInventory: body => data(api.post(`${base}/standalone-inventory/preview`, body)),
  saveStandaloneInventory: body => data(api.post(`${base}/standalone-inventory/save`, body)),
  inventoryCatalog: (configured = false) => data(api.get(`${base}/inventory-catalog${configured ? '?configured=true' : ''}`)),
  inventoryPurchaseEntry: (supplier,number) => data(api.get(`${base}/inventory-purchases/entry/${encodeURIComponent(supplier)}/${encodeURIComponent(number)}`)),
  inventoryPurchases: () => data(api.get(`${base}/inventory-purchases`)),
  saveInventoryPurchase: body => data(api.post(`${base}/inventory-purchases`,body)),
  exchangeOrder: number => data(api.get(`${base}/customer-exchanges/order/${encodeURIComponent(number)}`)),
  customerExchanges: () => data(api.get(`${base}/customer-exchanges`)),
  saveExchange: (body,id) => data(api.post(`${base}/customer-exchanges${id?'/'+encodeURIComponent(id)+'/actions':''}`,body)),
  returnOrder: number => data(api.get(`${base}/customer-returns/order/${encodeURIComponent(number)}`)),
  customerReturns: () => data(api.get(`${base}/customer-returns`)),
  saveCustomerReturn: (body, id) => data(api.post(`${base}/customer-returns${id ? '/'+encodeURIComponent(id)+'/confirm' : ''}`, body)),
  returnShippingQuote: (kind,id,orderNumber) => data(api.get(`${base}/customer-returns/shipping-quote/${kind}/${encodeURIComponent(id)}`, {params:{order_number:orderNumber}})),
  supplierReturn: body => data(api.post(`${base}/supplier-returns`, body)),
  context: () => data(api.get(`${base}/context`)),
  entities: kind => data(api.get(`${base}/entities/${kind}`)),
  addEntity: (kind, body) => data(api.post(`${base}/entities/${kind}`, body)),
  opening: body => data(api.post(`${base}/openings`, body)),
  finish: body => data(api.post(`${base}/finish`, body)),
  movements: () => data(api.get(`${base}/movements`)),
  movement: body => data(api.post(`${base}/movements`, body)),
  receipt: file => {
    const body = new FormData();
    body.append('file', file);
    return data(api.post(`${base}/receipts`, body));
  },
  obligations: (party_type, party_id) => data(api.get(`${base}/obligations`, {params:{party_type, party_id}})),
  receiptContent: id => data(api.get(`${base}/receipts/${encodeURIComponent(id)}`, {responseType:"blob"})),
  reports: () => data(api.get(`${base}/reports`)),
  costExposure: () => data(api.get(`${base}/cost-exposure`)),
  financialPosition: () => data(api.get(`${base}/financial-position`)),
  audit: () => data(api.get(`${base}/audit`))
};
export const entityKinds = [['employee', 'موظفين'], ['employee_custody', 'عهد الموظفين'], ['provider', 'منصات دفع'], ['pos_receivable', 'ذمم شبكة سابقة'], ['courier', 'شركات شحن'], ['store_driver', 'مناديب المتجر'], ['ad_account', 'منصات إعلانية'], ['supplier', 'موردين'], ['external_person', 'جهات خارجية'], ['bank', 'بنوك وأجهزة الشبكة'], ['cash', 'صناديق'], ['operating_expense', 'مصروفات تشغيلية'], ['owner_withdrawal', 'سحوبات المالك/المدير']];
export const requestId = () => globalThis.crypto.randomUUID();
export const feeIncomplete = issue => /(?:fee.*(?:policy|treatment|incomplete)|(?:refund|cancellation)_fee)/i.test(typeof issue === 'string' ? issue : issue?.code || '');
export const messageFor = error => feeIncomplete(error?.response?.data?.detail) ? 'إعداد العمولة غير مكتمل' : typeof error?.response?.data?.detail?.message === 'string' ? error.response.data.detail.message : 'تعذر إتمام العملية. احتفظنا بالمدخلات؛ حاول مرة أخرى.';
