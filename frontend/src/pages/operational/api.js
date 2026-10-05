import api from '../../lib/api';
const base = '/operational-balances';
const data = promise => promise.then(response => response.data);
export const operationalApi = {
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
  audit: () => data(api.get(`${base}/audit`))
};
export const entityKinds = [['employee', 'موظفين'], ['employee_custody', 'عهد الموظفين'], ['provider', 'منصات دفع'], ['courier', 'شركات شحن'], ['store_driver', 'مناديب المتجر'], ['ad_account', 'منصات إعلانية'], ['supplier', 'موردين'], ['external_person', 'جهات خارجية'], ['bank', 'بنوك'], ['cash', 'صناديق'], ['operating_expense', 'مصروفات تشغيلية'], ['owner_withdrawal', 'سحوبات المالك/المدير']];
export const requestId = () => globalThis.crypto.randomUUID();
export const feeIncomplete = issue => /(?:fee.*(?:policy|treatment|incomplete)|(?:refund|cancellation)_fee)/i.test(typeof issue === 'string' ? issue : issue?.code || '');
export const messageFor = error => feeIncomplete(error?.response?.data?.detail) ? 'إعداد العمولة غير مكتمل' : typeof error?.response?.data?.detail?.message === 'string' ? error.response.data.detail.message : 'تعذر إتمام العملية. احتفظنا بالمدخلات؛ حاول مرة أخرى.';
