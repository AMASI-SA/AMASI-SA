/* Run in the Console of the authenticated production Mezan tab after deployment.
 * Installing this helper makes no request and changes no order.
 */
(() => {
  'use strict';
  if (location.origin !== 'https://mezansalla.com') {
    throw new Error('افتح كونسول https://mezansalla.com');
  }
  const root = '/api/order-revision-tests';
  const id = value => {
    if (!/^[a-f0-9]{32}$/.test(value)) throw new Error('معرف الخطة غير صحيح');
    return value;
  };
  async function request(path, method = 'GET', body) {
    const token = localStorage.getItem('access_token');
    if (!token) throw new Error('سجّل الدخول بحساب مالك المتجر أولًا');
    let response;
    try {
      response = await fetch(root + path, {
        method, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
        ...(body === undefined ? {} : {body: JSON.stringify(body)})
      });
    } catch {
      throw new Error('انقطع الاتصال. لا تعِد التنفيذ؛ اقرأ حالة الخطة بنفس المعرف.');
    }
    if (!(response.headers.get('content-type') || '').includes('application/json')) {
      throw new Error('المسار لم يرجع JSON؛ تحقّق من نشر مسار الاختبار.');
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result?.detail?.code || `HTTP ${response.status}`);
    return result;
  }
  window.mezanOrderTest = Object.freeze({
    status: () => request('/status'),
    prepare: (manifest, testCase) => request('/plans', 'POST', {manifest, case: testCase}),
    read: planId => request(`/plans/${id(planId)}`),
    execute: planId => request(`/plans/${id(planId)}/execute`, 'POST')
  });
  console.info('جاهز. ابدأ بـ await mezanOrderTest.status()');
})();
