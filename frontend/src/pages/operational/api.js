import api from '../../lib/api';
const base = '/operational-balances';
const data = promise => promise.then(response => response.data);
export const operationalApi = {
  previewDeliveryAfterStart: (body, config = {}) => data(api.post(`${base}/delivery-after-start/preview`, body, {timeout: 30000, ...config})),
  confirmDeliveryAfterStart: body => data(api.post(`${base}/delivery-after-start/confirm`, body)),
  customerWalletContext: (config = {}) => data(api.get(`${base}/customer-wallet/context`, config)),
  saveCustomerWalletSnapshot: body => data(api.post(`${base}/customer-wallet/snapshot`, body)),
  receivingInvoiceCoverage: (config = {}) => data(api.get(`${base}/supplier-invoice-review`, {...config, params: {...config.params, receiving_only: true}})),
  receivingCorrectionPreview: (invoiceId, asOf, revision, config = {}) => data(api.get(
    `${base}/supplier-invoice-review/receiving/${encodeURIComponent(invoiceId)}/correction-preview`,
    {...config, params: {...config.params, expected_as_of: asOf, expected_revision: revision}})),
  supplierInvoiceReview: (config = {}) => data(api.get(`${base}/supplier-invoice-review`, config)),
  providerHistoryReviews: (config = {}) => data(api.get(`${base}/provider-history-reviews`, config)),
  approveProviderHistory: body => data(api.post(`${base}/provider-history-reviews/approve`, body)),
  nativePurchaseCandidates: (id, config = {}) => data(api.get(`${base}/native-purchase-links/${encodeURIComponent(id)}/candidates`, config)),
  previewNativePurchaseLink: (body, config = {}) => data(api.post(`${base}/native-purchase-links/preview`, body, config)),
  approveNativePurchaseLink: body => data(api.post(`${base}/native-purchase-links/approve`, body)),
  salaryStartContext: (config = {}) => data(api.get(`${base}/salary-start/context`, config)),
  previewSalaryStart: (body, config = {}) => data(api.post(`${base}/salary-start/preview`, body, config)),
  approveSalaryStart: body => data(api.post(`${base}/salary-start/approve`, body)),
  adFundingSettings: (config = {}) => data(api.get(`${base}/ad-funding-settings`, config)),
  saveAdFundingSettings: body => data(api.post(`${base}/ad-funding-settings`, body)),
  inventoryOpeningContext: (config = {}) => data(api.get(`${base}/inventory-opening/context`, config)),
  previewInventoryOpening: (body, config = {}) => data(api.post(`${base}/inventory-opening/preview`, body, config)),
  approveInventoryOpening: body => data(api.post(`${base}/inventory-opening/approve`, body)),
  adAccountSelection: (config = {}) => data(api.get(`${base}/ad-account-selection`, config)),
  saveAdAccountSelection: body => data(api.post(`${base}/ad-account-selection`, body)),
  inventoryAvailability: (config = {}) => data(api.get(`${base}/inventory`, config)),
  supplierAdjustmentEntry: (supplier,number,config = {}) => data(api.get(`${base}/supplier-adjustments/entry/${encodeURIComponent(supplier)}/${encodeURIComponent(number)}`, config)),
  saveSupplierAdjustment: body => data(api.post(`${base}/supplier-adjustments`,body)),
  standaloneInventoryContext: () => data(api.get(`${base}/standalone-inventory/context`)),
  standaloneInventoryControl: body => data(api.post(`${base}/standalone-inventory/control`, body)),
  previewStandaloneInventory: body => data(api.post(`${base}/standalone-inventory/preview`, body)),
  saveStandaloneInventory: body => data(api.post(`${base}/standalone-inventory/save`, body)),
  inventoryCatalog: (configured = false, config = {}) => data(api.get(`${base}/inventory-catalog${configured ? '?configured=true' : ''}`, config)),
  inventoryPurchaseEntry: (supplier,number,config = {}) => data(api.get(`${base}/inventory-purchases/entry/${encodeURIComponent(supplier)}/${encodeURIComponent(number)}`, config)),
  inventoryPurchases: (config = {}) => data(api.get(`${base}/inventory-purchases`, config)),
  saveInventoryPurchase: body => data(api.post(`${base}/inventory-purchases`,body)),
  exchangeOrder: (number, config = {}) => data(api.get(`${base}/customer-exchanges/order/${encodeURIComponent(number)}`, config)),
  customerExchanges: (config = {}) => data(api.get(`${base}/customer-exchanges`, config)),
  saveExchange: (body,id) => data(api.post(`${base}/customer-exchanges${id?'/'+encodeURIComponent(id)+'/actions':''}`,body)),
  returnOrder: (number, config = {}) => data(api.get(`${base}/customer-returns/order/${encodeURIComponent(number)}`, config)),
  customerReturns: (config = {}) => data(api.get(`${base}/customer-returns`, config)),
  saveCustomerReturn: (body, id) => data(api.post(`${base}/customer-returns${id ? '/'+encodeURIComponent(id)+'/confirm' : ''}`, body)),
  returnShippingQuote: (kind,id,orderNumber,config = {}) => data(api.get(`${base}/customer-returns/shipping-quote/${kind}/${encodeURIComponent(id)}`, {...config, params:{...config.params,order_number:orderNumber}})),
  supplierReturn: body => data(api.post(`${base}/supplier-returns`, body)),
  context: (config = {}) => data(api.get(`${base}/context`, {timeout: 30000, ...config})),
  entities: (kind, config = {}) => data(api.get(`${base}/entities/${kind}`, config)),
  addEntity: (kind, body) => data(api.post(`${base}/entities/${kind}`, body)),
  opening: body => data(api.post(`${base}/openings`, body)),
  finish: body => data(api.post(`${base}/finish`, body)),
  movements: (config = {}) => data(api.get(`${base}/movements`, config)),
  movement: body => data(api.post(`${base}/movements`, body)),
  receipt: file => {
    const body = new FormData();
    body.append('file', file);
    return data(api.post(`${base}/receipts`, body));
  },
  obligations: (party_type, party_id, config = {}) => data(api.get(`${base}/obligations`, {...config, params:{...config.params, party_type, party_id}})),
  receiptContent: id => data(api.get(`${base}/receipts/${encodeURIComponent(id)}`, {responseType:"blob"})),
  reports: (config = {}) => data(api.get(`${base}/reports`, {timeout: 45000, ...config})),
  refreshBalances: (config = {}) => data(api.post(`${base}/refresh`, {visible: true}, {timeout: 90000, ...config})),
  costExposure: (config = {}) => data(api.get(`${base}/cost-exposure`, {timeout: 45000, ...config})),
  financialPosition: (config = {}) => data(api.get(`${base}/financial-position`, {timeout: 60000, ...config})),
  dailyFinancialMovements: (params, config = {}) => data(api.get(`${base}/daily-financial-movements`, {timeout: 30000, ...config, params})),
  audit: (config = {}) => data(api.get(`${base}/audit`, {timeout: 30000, ...config}))
};
export const entityKinds = [['employee', 'موظفين'], ['employee_custody', 'عهد الموظفين'], ['provider', 'منصات دفع'], ['pos_receivable', 'ذمم شبكة سابقة'], ['courier', 'شركات شحن'], ['store_driver', 'مناديب المتجر'], ['ad_account', 'منصات إعلانية'], ['supplier', 'موردين'], ['external_person', 'جهات خارجية'], ['bank', 'بنوك وأجهزة الشبكة'], ['cash', 'صناديق'], ['operating_expense', 'مصروفات تشغيلية'], ['owner_withdrawal', 'سحوبات المالك/المدير']];
export const requestId = () => globalThis.crypto.randomUUID();
export const feeIncomplete = issue => /(?:fee.*(?:policy|treatment|incomplete)|(?:refund|cancellation)_fee)/i.test(typeof issue === 'string' ? issue : issue?.code || '');
export const messageFor = error => feeIncomplete(error?.response?.data?.detail) ? 'إعداد العمولة غير مكتمل' : typeof error?.response?.data?.detail?.message === 'string' ? error.response.data.detail.message : 'تعذر إتمام العملية. احتفظنا بالمدخلات؛ حاول مرة أخرى.';
