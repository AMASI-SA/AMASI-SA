import http from '../../lib/api';
import { demandRead } from '../../lib/demandRead';

const workspace = '/operational-balances/workspace';
const access = '/operational-balances/access';
const data = promise => promise.then(response => response.data);
const read = config => ({ timeout: 30000, ...config });
const demandGet = (path, params, config) => demandRead(request => http.get(`${workspace}/${path}`, {
  ...request, params: { ...params, demand: true },
}), read(config));
const optionalDemandGet = (path, params, config) => config?.demand
  ? demandGet(path, params, config)
  : data(http.get(`${workspace}/${path}`, { ...read(config), ...(params && { params }) }));

export const simpleBalancesApi = {
  bootstrap: config => demandGet('bootstrap', undefined, config),
  home: config => demandGet('home', undefined, config),
  directory: (params, config) => demandGet('directory', params, config),
  options: (params, config) => demandGet('options', params, config),
  position: (params, config) => demandGet('position', params, config),
  selection: (kind, id, config) => demandGet(`selection/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, undefined, config),
  context: (config, { homePreview = false, directoryOnly = false } = {}) => data(http.get(`${workspace}/context`, { ...read(config), params: { ad_bank_fx: true, ...(directoryOnly ? { directory_only: true } : { provider_positions: true }), ...(homePreview ? { home_preview: true } : {}) } })),
  accountGroup: (kind, config) => data(http.get(`${workspace}/accounts/${encodeURIComponent(kind)}`, read(config))),
  resource: (kind, id, config) => optionalDemandGet(`resource/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
    kind === 'ad_account' ? { ad_bank_fx: true } : undefined, config),
  parties: (params, config) => data(http.get(`${workspace}/parties`, { ...read(config), params })),
  movements: (params, config) => optionalDemandGet('movements', params, config),
  ledger: (kind, id, params, config) => optionalDemandGet(`ledger/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
    kind === 'ad_account' ? { ...params, ad_bank_fx: true } : params, config),
  supplierInvoice: (supplier, invoice, config) => data(http.get(
    `${workspace}/supplier-invoices/${encodeURIComponent(supplier)}/${encodeURIComponent(invoice)}`, read(config),
  )),
  history: (kind, id, params, config) => optionalDemandGet(`history/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, params, config),
  statement: (kind, id, params, config) => optionalDemandGet(`statements/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, params, config),
  reviews: (params, config) => data(http.get(`${workspace}/reviews`, { ...read(config), params })),
  review: (id, config) => data(http.get(`${workspace}/reviews/${encodeURIComponent(id)}`, read(config))),
  reviewOptions: (id, config) => data(http.get(`${workspace}/reviews/${encodeURIComponent(id)}/options`, read(config))),
  reviewBankMovements: (id, params, config) => data(http.get(`${workspace}/reviews/${encodeURIComponent(id)}/bank-movements`, { ...read(config), params })),
  bindPos: body => data(http.post('/operational-balances/pos-receivable-binding', body, { timeout: 30000 })),
  reviewReceipt: (id, config) => data(http.get(`${workspace}/reviews/${encodeURIComponent(id)}/receipt`, { ...read(config), responseType: 'blob' })),
  reviewDecision: (id, body) => data(http.post(`${workspace}/reviews/${encodeURIComponent(id)}`, body, { timeout: 30000 })),
  quoteAd: body => data(http.post(`${workspace}/ad-funding-quote`, body, { timeout: 30000 })),
  entry: body => data(http.post(`${workspace}/entries`, body, { timeout: 30000 })),
  reverseMovement: (id, body) => data(http.post(`${workspace}/movements/${encodeURIComponent(id)}/reverse`, body, { timeout:30000 })),
  registerPending: (id, body) => data(http.post(`${workspace}/pending-commands/${encodeURIComponent(id)}`, body, { timeout: 30000 })),
  pendingCommand: (id, scope, config) => data(http.get(`${workspace}/pending-commands/${encodeURIComponent(id)}`, {
    ...read(config), params: { expected_session_scope: scope },
  })),
  discardPending: (id, scope) => data(http.post(`${workspace}/pending-commands/${encodeURIComponent(id)}/discard`, {
    expected_session_scope: scope,
  }, { timeout: 30000 })),
  retirePending: (id, scope) => data(http.delete(`${workspace}/pending-commands/${encodeURIComponent(id)}`, {
    timeout: 30000, data: { expected_session_scope: scope },
  })),
  refundOrder: (number, config) => data(http.get(`${workspace}/refunds/order/${encodeURIComponent(number)}`, read(config))),
  customerAdjustments: (params, config) => data(http.get(`${workspace}/customer-adjustments`, { ...read(config), params })),
  refunds: (params, config) => data(http.get(`${workspace}/refunds`, { ...read(config), params })),
  refund: (id, config) => data(http.get(`${workspace}/refunds/${encodeURIComponent(id)}`, read(config))),
  requestRefund: body => data(http.post(`${workspace}/refunds`, body, { timeout: 30000 })),
  recordRefundExecution: body => data(http.post(`${workspace}/refunds/record-execution`, body, { timeout: 30000 })),
  uploadCustomerReceipt: body => data(http.post(`${workspace}/refund-receipts`, body, { timeout: 30000 })),
  customerReceipt: (id, config) => data(http.get(`${workspace}/refund-receipts/${encodeURIComponent(id)}`, { ...read(config), responseType: 'blob' })),
  customerCollectionOrder: (number, config) => data(http.get(`${workspace}/customer-collections/order/${encodeURIComponent(number)}`, read(config))),
  customerCollections: (params, config) => data(http.get(`${workspace}/customer-collections`, { ...read(config), params })),
  quoteCustomerCollection: body => data(http.post(`${workspace}/customer-collections/quote`, body, { timeout: 30000 })),
  recordCustomerCollection: body => data(http.post(`${workspace}/customer-collections`, body, { timeout: 30000 })),
  confirmRefund: (id, body) => data(http.post(`${workspace}/refunds/${encodeURIComponent(id)}/confirm`, body, { timeout: 30000 })),
  reconcileRefund: (id, body) => data(http.post(`${workspace}/refunds/${encodeURIComponent(id)}/reconcile`, body, { timeout: 30000 })),
  cancelRefund: (id, body) => data(http.post(`${workspace}/refunds/${encodeURIComponent(id)}/cancel`, body, { timeout: 30000 })),
  accessCatalog: config => data(http.get(`${access}/catalog`, read(config))),
  accessUsers: config => data(http.get(`${access}/users`, read(config))),
  userAccess: (id, config) => data(http.get(`${access}/users/${encodeURIComponent(id)}`, read(config))),
  saveUserAccess: (id, body) => data(http.put(`${access}/users/${encodeURIComponent(id)}`, body, { timeout: 30000 })),
};

export function simpleBalanceError(error) {
  const status = error?.response?.status;
  const detail = error?.response?.data?.detail;
  if (detail?.not_applied === true && typeof detail.message === 'string') return detail.message;
  if (status === 401) return 'انتهت جلسة الدخول. سجّل الدخول مجددًا لعرض حساباتك.';
  if (status === 403) return 'لا توجد صلاحية لهذا الإجراء أو تغيرت صلاحياتك. حدّث الصفحة لمراجعة المتاح لك.';
  if (status === 404) return 'هذا الحساب غير متاح ضمن صلاحياتك الحالية.';
  if (typeof detail?.message === 'string') return detail.message;
  if (status === 409) return 'تغيرت البيانات أو الصلاحيات. أعد تحميلها قبل متابعة الإجراء.';
  if (typeof detail === 'string' && /[\u0600-\u06ff]/.test(detail)) return detail;
  return error?.localMessage || 'تعذر إتمام العملية. بقيت المدخلات محفوظة؛ يمكنك إعادة المحاولة.';
}
