import { afterBalance, balanceText, decimalAmount, entryChoices, isPage, minorUnits, money, permittedOperations,
  movementDate, movementRecorder, movementTitle, signedResourceBalance, canReadStatement, isStatement } from './simpleBalancesModel';

test('amounts retain decimal accuracy, Arabic digits, and large integer money', () => {
  expect(decimalAmount('١٠٠٫١٠')).toBe('100.10');
  expect(minorUnits('0.29')).toBe(29n);
  expect(decimalAmount('0')).toBeNull();
  expect(decimalAmount('12.001')).toBeNull();
  expect(money('9007199254740993.25')).toBe('9,007,199,254,740,993.25');
  expect(balanceText(null)).toBe('الرصيد غير متاح');
  expect(balanceText({ amount: '25.00', side: 'for_party' }, true)).toBe('−25 ريال');
});

test('paying 2000 against a 3000 due leaves 1000 for the party', () => {
  expect(afterBalance({ amount: '3000.00', side: 'for_party' }, '2000.00', 'for_us'))
    .toEqual({ amount: '1000.00', side: 'for_party', visible: true });
  expect(afterBalance(null, '2000.00', 'for_us')).toBeNull();
});

test('external payment has no extra reason and needs its own explicit grant', () => {
  const granted = new Set(['external.pay', 'external.salary', 'external.charity_owner']);
  expect(entryChoices('external_person', 'for_us', granted)).toEqual([{ reason: '', label: 'تسليم مبلغ للجهة', operation: 'external.pay' }]);
  expect(entryChoices('external_person', 'for_party', granted).map(row => row.reason)).toEqual(['salary_settlement', 'charity']);
  expect(entryChoices('external_person', 'for_us', new Set(['external.salary']))).toEqual([]);
});

test('resource restrictions cannot be widened by the global operation catalogue', () => {
  const context = { operations: [{ key: 'supplier.payment' }, { key: 'supplier.adjustment' }] };
  expect([...permittedOperations(context, { operations: ['supplier.payment'] })]).toEqual(['supplier.payment']);
  expect([...permittedOperations(context, { operations: [] })]).toEqual([]);
  expect(entryChoices('supplier', 'for_us', permittedOperations(context, { operations: ['supplier.payment'] })).map(row => row.reason)).toEqual(['payment']);
});

test('a stale or reduced result page accepts only the exact server clamp', () => {
  const result = { items: [{ id: 'remaining-row' }], page: 2, page_size: 5, total: 6, total_pages: 2 };
  expect(isPage(result, 9)).toBe(true);
  expect(isPage({ ...result, page: 1, total: 1, total_pages: 1 }, 3)).toBe(true);
  expect(isPage({ ...result, page: 1, items: [], total: 0, total_pages: 1 }, 9)).toBe(true);
  expect(isPage({ ...result, page: 1 }, 9)).toBe(false);
  expect(isPage(result, 1)).toBe(false);
  expect(isPage({ ...result, page: 1, total_pages: 3 }, 3)).toBe(false);
});

test('an explicit focus may choose a different valid page but never one outside the result', () => {
  const result = { items: [{ id: 'backdated-row' }], page: 2, page_size: 5, total: 6, total_pages: 2 };
  expect(isPage(result, 1, true)).toBe(true);
  expect(isPage({ ...result, page: 3 }, 1, true)).toBe(false);
  expect(isPage({ ...result, total_pages: 0 }, 1, true)).toBe(false);
});

test('compact dates use real Riyadh time and never manufacture a time for a business date', () => {
  expect(movementDate({ business_date: '2026-11-18' })).toBe('11-18');
  expect(movementDate({ business_date: '2026-11-18', recorded_at: '2026-11-18T22:35:00Z' })).toBe('11-18');
  expect(movementDate({ business_date: '2026-11-19', recorded_at: '2026-11-18T22:35:00Z' })).toBe('11-19 01:35');
  expect(movementDate({ business_date: '2026-11-18' }, true)).toBe('2026-11-18');
  expect(movementDate({ recorded_at: '2026-11-18T22:35:00Z' }, true)).toBe('2026-11-19 01:35 · الرياض');
  expect(movementDate({ business_date: '2026-11-10', recorded_at: '2026-11-19T12:35:00Z' }, true)).toBe('2026-11-10 · سُجّلت 2026-11-19 15:35 · الرياض');
});

test('movement titles name the known customer, order, or source without inventing a platform', () => {
  expect(movementTitle({ customer_name: 'خالد', order_number: '123', description: 'استلام مبلغ' })).toBe('خالد');
  expect(movementTitle({ order_number: '123', description: 'دفع مبلغ' })).toBe('طلب 123');
  expect(movementTitle({ source_label: 'بنك الإنماء', description: 'استلام مبلغ' })).toBe('بنك الإنماء');
  expect(movementTitle({ automatic_history: 'delivery' }, true)).toBe('آخر أجرة توصيل');
  expect(movementTitle({ description: 'ملاحظة موثقة' })).toBe('ملاحظة موثقة');
  expect(movementTitle({ counterparty_name: 'سالم', source_label: 'الإنماء', description: 'دفع مبلغ' })).toBe('سالم');
  expect(movementTitle({ description: 'فاتورة توريد موثقة', source_label: 'الإنماء' })).toBe('فاتورة توريد موثقة');
});

test('only verified automatic provenance attributes a movement to the system', () => {
  expect(movementRecorder({ automatic_history: 'salary', recorded_by: null })).toBe('النظام تلقائيًا');
  expect(movementRecorder({ automatic_history: 'delivery' })).toBe('النظام تلقائيًا');
  expect(movementRecorder({ description: 'راتب تلقائي' })).toBe('غير موثق');
  expect(movementRecorder({ reason: 'salary', recorded_by: { name: 'محمد' } })).toBe('محمد');
});

test('signed display respects party dues and available cash without changing entry calculations', () => {
  expect(signedResourceBalance({ kind: 'employee', access: { balance: true }, balance: { visible: true, side: 'for_us', amount: '120.00' } })).toBe('-120.00');
  expect(signedResourceBalance({ kind: 'bank', access: { balance: true }, balance: { visible: true, side: 'for_us', amount: '120.00' } })).toBe('120.00');
  expect(signedResourceBalance({ kind: 'employee', access: { balance: false }, balance: { amount: '100', side: 'for_party' } })).toBeNull();
});

test('statement rights and print rights stay separate and old grants default to denied', () => {
  const resource = { access: { movements: 'all', balance: true } };
  expect(canReadStatement({}, resource)).toBe(false);
  expect(canReadStatement({}, { access: { movements: 'own', report: true } })).toBe(true);
  expect(canReadStatement({}, { access: { movements: 'none', report: true, print: true } })).toBe(false);
  expect(canReadStatement({ actor: { is_owner: true } }, resource)).toBe(true);
});

test('statement validation accepts complete unpaginated periods but refuses the wrong resource or dates', () => {
  const resource = { kind: 'employee', id: 'one' };
  const query = { date_from: '2026-11-01', date_to: '2026-11-18' };
  const statement = { resource, ...query, items: Array.from({ length: 12 }, (_, id) => ({ id, amount: '100.00' })),
    scope: 'all', currency: 'SAR', opening_balance: '0.00', closing_balance: '1200.00', totals: { for_party: '1200.00', for_us: '0.00' }, can_print: false };
  expect(isStatement(statement, resource, query)).toBe(true);
  expect(isStatement({ ...statement, resource: { ...resource, id: 'other' } }, resource, query)).toBe(false);
  expect(isStatement({ ...statement, date_from: '2026-10-01' }, resource, query)).toBe(false);
});

test('prepaid shipping on the wallet names the approved carrier and order', () => {
  const row = { prepaid_shipping: true, counterparty_name: 'أرامكس', order_number: '123', customer_name: 'عميل' };
  expect(movementTitle(row, true)).toBe('أرامكس · 123');
  expect(movementTitle({ ...row, prepaid_shipping: false })).toBe('عميل');
});
