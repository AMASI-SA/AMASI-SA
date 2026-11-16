import { afterBalance, balanceText, decimalAmount, entryChoices, minorUnits, money, permittedOperations } from './simpleBalancesModel';

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
