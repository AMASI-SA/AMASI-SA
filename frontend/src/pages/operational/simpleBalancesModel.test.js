import { afterBalance, balanceText, decimalAmount, entryChoices, isPage, minorUnits, money, permittedOperations } from './simpleBalancesModel';

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
