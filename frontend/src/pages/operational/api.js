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
  reports: () => data(api.get(`${base}/reports`)),
  audit: () => data(api.get(`${base}/audit`))
};
export const entityKinds = [['employee', 'موظفين'], ['provider', 'منصات دفع'], ['courier', 'شركات شحن'], ['store_driver', 'مناديب المتجر'], ['ad_account', 'منصات إعلانية'], ['supplier', 'موردين'], ['external_person', 'جهات خارجية'], ['bank', 'بنوك'], ['cash', 'صناديق']];
export const requestId = () => globalThis.crypto.randomUUID();
export const messageFor = error => typeof error?.response?.data?.detail?.message === 'string' ? error.response.data.detail.message : 'تعذر إتمام العملية. احتفظنا بالمدخلات؛ حاول مرة أخرى.';
