import http from '../../lib/api';

const workspace = '/operational-balances/workspace';
const access = '/operational-balances/access';
const data = promise => promise.then(response => response.data);
const read = config => ({ timeout: 30000, ...config });

export const simpleBalancesApi = {
  context: config => data(http.get(`${workspace}/context`, read(config))),
  parties: (params, config) => data(http.get(`${workspace}/parties`, { ...read(config), params })),
  movements: (params, config) => data(http.get(`${workspace}/movements`, { ...read(config), params })),
  ledger: (kind, id, params, config) => data(http.get(
    `${workspace}/ledger/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
    { ...read(config), params },
  )),
  entry: body => data(http.post(`${workspace}/entries`, body, { timeout: 30000 })),
  accessCatalog: config => data(http.get(`${access}/catalog`, read(config))),
  accessUsers: config => data(http.get(`${access}/users`, read(config))),
  userAccess: (id, config) => data(http.get(`${access}/users/${encodeURIComponent(id)}`, read(config))),
  saveUserAccess: (id, body) => data(http.put(`${access}/users/${encodeURIComponent(id)}`, body, { timeout: 30000 })),
};

export function simpleBalanceError(error) {
  const status = error?.response?.status;
  if (status === 401) return 'انتهت جلسة الدخول. سجّل الدخول مجددًا لعرض حساباتك.';
  if (status === 403) return 'لا توجد صلاحية لهذا الإجراء أو تغيرت صلاحياتك. حدّث الصفحة لمراجعة المتاح لك.';
  if (status === 404) return 'هذا الحساب غير متاح ضمن صلاحياتك الحالية.';
  const detail = error?.response?.data?.detail;
  if (typeof detail?.message === 'string') return detail.message;
  if (status === 409) return 'تغيرت البيانات أو الصلاحيات. أعد تحميلها قبل متابعة الإجراء.';
  if (typeof detail === 'string' && /[\u0600-\u06ff]/.test(detail)) return detail;
  return error?.localMessage || 'تعذر إتمام العملية. بقيت المدخلات محفوظة؛ يمكنك إعادة المحاولة.';
}
