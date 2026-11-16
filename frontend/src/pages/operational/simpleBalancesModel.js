export const BALANCE_PAGE_SIZE = 5;

export const resourceLabels = {
  employee: 'الموظفون', supplier: 'الموردون', external_person: 'الجهات الخارجية',
  employee_custody: 'عهد الموظفين', bank: 'البنوك', cash: 'الصناديق',
  provider: 'مزودو الدفع', courier: 'شركات الشحن', store_driver: 'الموصلون',
  ad_account: 'حسابات الإعلانات', pos_receivable: 'تحصيلات الشبكة',
  operating_expense: 'المصروفات', owner_withdrawal: 'حسابات المالك',
};

export const resourceKey = resource => JSON.stringify([resource.kind, resource.id]);
export const isMoneyAccount = kind => kind === 'bank' || kind === 'cash';

export function minorUnits(value) {
  const text = String(value ?? '').trim()
    .replace(/[٠-٩]/g, digit => String('٠١٢٣٤٥٦٧٨٩'.indexOf(digit)))
    .replace(/[۰-۹]/g, digit => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit)))
    .replace(/٫/g, '.');
  if (!/^-?\d{1,18}(?:\.\d{1,2})?$/.test(text)) return null;
  const negative = text.startsWith('-');
  const [whole, fraction = ''] = text.replace(/^-/, '').split('.');
  const units = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  return negative ? -units : units;
}

export function decimalAmount(value) {
  const units = minorUnits(value);
  if (units === null || units <= 0n) return null;
  return `${units / 100n}.${String(units % 100n).padStart(2, '0')}`;
}

export function money(value) {
  const units = minorUnits(value);
  if (units === null) return 'غير متاح';
  const absolute = units < 0n ? -units : units;
  const whole = (absolute / 100n).toLocaleString('en-US');
  const fraction = absolute % 100n;
  return `${units < 0n ? '−' : ''}${whole}${fraction ? `.${String(fraction).padStart(2, '0')}` : ''}`;
}

export function balanceText(balance, account = false, currency = 'SAR') {
  if (!balance || balance.visible === false || minorUnits(balance.amount) === null) return 'الرصيد غير متاح';
  const unit = currency === 'SAR' ? 'ريال' : currency;
  if (account) return `${balance.side === 'for_party' && minorUnits(balance.amount) > 0n ? '−' : ''}${money(balance.amount)} ${unit}`;
  return `${balance.side === 'for_party' ? 'له ' : balance.side === 'for_us' ? 'عليه ' : 'متوازن · '}${money(balance.amount)} ${unit}`;
}

export function afterBalance(balance, amount, side) {
  if (!balance || balance.visible === false) return null;
  const current = minorUnits(balance.amount), delta = minorUnits(amount);
  if (current === null || delta === null) return null;
  const signed = (balance.side === 'for_us' ? -current : current) + (side === 'for_us' ? -delta : delta);
  const absolute = signed < 0n ? -signed : signed;
  return { amount: `${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`, side: signed > 0n ? 'for_party' : signed < 0n ? 'for_us' : 'balanced', visible: true };
}

export function permittedOperations(context, resource) {
  const global = new Set((context?.operations || []).map(operation => typeof operation === 'string' ? operation : operation.key));
  const scoped = resource?.operations;
  return new Set((Array.isArray(scoped) ? scoped.map(operation => typeof operation === 'string' ? operation : operation.key) : [...global]).filter(key => global.has(key)));
}

export function entryChoices(kind, side, operations) {
  const has = key => operations.has(key);
  if (isMoneyAccount(kind)) return has('accounts.transfer') ? [{ reason: 'transfer', label: 'تحويل بين الحسابات', operation: 'accounts.transfer' }] : [];
  if (kind === 'external_person') {
    if (side === 'for_us') return has('external.pay') ? [{ reason: '', label: 'تسليم مبلغ للجهة', operation: 'external.pay' }] : [];
    if (side !== 'for_party') return [];
    return [
      ['receipt', 'تسوية مديونية', 'external.receive'],
      ['salary_settlement', 'صرف راتب موظف', 'external.salary'],
      ['charity', 'صدقات', has('external.charity_company') ? 'external.charity_company' : 'external.charity_owner'],
      ['household', 'مصروفات منزلية', 'external.household'],
      ['personal', 'مصروفات شخصية', 'external.personal'],
    ].filter(([, , operation]) => has(operation)).map(([reason, label, operation]) => ({ reason, label, operation }));
  }
  if (kind === 'employee' || kind === 'supplier') {
    const choices = side === 'for_party' ? [['accrual', 'تسجيل مستحق له'], ['collection', 'استلمت منه']]
      : side === 'for_us' ? [['payment', 'دفعت له'], ['adjustment', 'خصم من مستحقه']] : [];
    return choices.filter(([reason]) => has(`${kind}.${reason}`)).map(([reason, label]) => ({ reason, label, operation: `${kind}.${reason}` }));
  }
  const mapping = {
    employee_custody: [['for_us', 'payment', 'صرف عهدة', 'custody.fund'], ['for_party', 'collection', 'رد المتبقي من العهدة', 'custody.return']],
    owner_withdrawal: [['for_us', 'payment', 'سحب مبلغ للمالك', 'owner.withdraw'], ['for_party', 'collection', 'إعادة مبلغ من المالك', 'owner.return']],
    operating_expense: [['for_us', 'payment', 'تسجيل مصروف', 'expense.pay']],
    ad_account: [['for_us', 'payment', 'دفعة لحساب الإعلان', 'ads.fund']],
    courier: [['for_party', 'collection', 'استلام تحصيل', 'courier.receive'], ['for_us', 'payment', 'دفع مستحق', 'courier.pay']],
    store_driver: [['for_party', 'collection', 'استلام من الموصل', 'courier.receive'], ['for_us', 'payment', 'دفع مستحق', 'courier.pay']],
    provider: [['for_party', 'collection', 'تسجيل تحصيل', 'provider.settle']],
    pos_receivable: [['for_party', 'collection', 'تسجيل تحصيل الشبكة', 'provider.settle']],
  };
  return (mapping[kind] || []).filter(([direction, , , operation]) => direction === side && has(operation))
    .map(([, reason, label, operation]) => ({ reason, label, operation }));
}

export function isPage(value, requestedPage, focused = false) {
  return Boolean(value && Array.isArray(value.items) && value.items.length <= BALANCE_PAGE_SIZE
    && value.page_size === BALANCE_PAGE_SIZE && Number.isInteger(value.page) && value.page >= 1
    && (focused || value.page === requestedPage) && Number.isInteger(value.total) && value.total >= value.items.length
    && Number.isInteger(value.total_pages) && value.total_pages >= 0);
}

export function validContext(value) {
  return Boolean(value?.actor?.id && typeof value.session_scope === 'string' && value.session_scope
    && Array.isArray(value.resources) && Array.isArray(value.operations)
    && value.resources.every(resource => typeof resource.kind === 'string' && resource.id && typeof resource.name === 'string' && resource.access));
}
