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

export function movementDate(row, full = false) {
  // Date-only business records do not establish a time. A timestamp without a
  // timezone is likewise not evidence of the user's local timezone.
  const timestamp = row?.recorded_at;
  if (typeof timestamp === 'string' && /T\d{2}:\d{2}.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(timestamp)) {
    const date = new Date(timestamp);
    if (!Number.isNaN(date.getTime())) {
      const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Riyadh', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
      }).formatToParts(date).map(part => [part.type, part.value]));
      const recordedDay = `${parts.year}-${parts.month}-${parts.day}`;
      const businessDay = /^\d{4}-\d{2}-\d{2}$/.test(row?.business_date || '') ? row.business_date : null;
      if (businessDay && businessDay !== recordedDay) {
        return full ? `${businessDay} · سُجّلت ${recordedDay} ${parts.hour}:${parts.minute} · الرياض` : businessDay.slice(5);
      }
      return `${full ? `${parts.year}-` : ''}${parts.month}-${parts.day} ${parts.hour}:${parts.minute}${full ? ' · الرياض' : ''}`;
    }
  }
  const date = row?.business_date;
  return /^\d{4}-\d{2}-\d{2}$/.test(date || '') ? (full ? date : date.slice(5)) : 'تاريخ غير موثق';
}

export function movementTitle(row, compact = false) {
  if (compact && row.automatic_history === 'salary') return 'آخر راتب مسجل';
  if (compact && row.automatic_history === 'delivery') return 'آخر أجرة توصيل';
  if (row.prepaid_shipping && row.counterparty_name && row.order_number) return `${row.counterparty_name} · ${row.order_number}`;
  if (row.customer_name) return row.customer_name;
  if (row.order_number) return `طلب ${row.order_number}`;
  if (row.counterparty_name) return row.counterparty_name;
  const description = row.description || '';
  if (description && !/^(استلام مبلغ|دفع مبلغ|مستحق من المصدر|تسجيل مستحق)(?:\s|$)/.test(description)) return description;
  return row.source_label || row.party_name || description || 'حركة مالية';
}

export function movementRecorder(row) {
  if (row.system_recorded || ['salary', 'delivery'].includes(row.automatic_history)) return 'النظام تلقائيًا';
  const name = row.recorded_by?.name?.trim();
  return name && name !== 'غير موثق' ? name : 'اسم الموظف غير متوفر';
}

export function ledgerDisplayAmount(value, kind) {
  if (kind !== 'ad_account') return value;
  const units = minorUnits(value);
  if (units === null) return null;
  const absolute = units < 0n ? -units : units;
  return `${units > 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`;
}

export function signedResourceBalance(resource) {
  const balance = resource?.balance;
  if (resource?.access?.balance !== true || !balance || balance.visible === false) return null;
  const amount = minorUnits(balance.amount);
  if (amount === null) return null;
  const cash = isMoneyAccount(resource.kind) || ['employee_custody', 'ad_account'].includes(resource.kind);
  const negative = cash ? balance.side === 'for_party' : balance.side === 'for_us';
  const value = negative ? -amount : amount;
  const absolute = value < 0n ? -value : value;
  return `${value < 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`;
}

export function canReadStatement(context, resource) {
  return Boolean(resource && resource.access?.movements !== 'none'
    && (context?.actor?.is_owner === true || resource.access?.report === true));
}

export function isStatement(value, resource, query) {
  return Boolean(value && value.resource?.kind === resource.kind && value.resource?.id === resource.id
    && value.date_from === query.date_from && value.date_to === query.date_to
    && ['all', 'own'].includes(value.scope) && typeof value.currency === 'string'
    && Array.isArray(value.items) && value.items.every(row => row.id != null && minorUnits(row.amount) !== null)
    && minorUnits(value.totals?.for_party) !== null && minorUnits(value.totals?.for_us) !== null
    && [value.opening_balance, value.closing_balance].every(balance => balance === null || minorUnits(balance) !== null)
    && typeof value.can_print === 'boolean');
}

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
    pos_receivable: [['for_party', 'collection', 'موازنة / تحويل إلى بنك', 'provider.settle']],
  };
  return (mapping[kind] || []).filter(([direction, , , operation]) => direction === side && has(operation))
    .map(([, reason, label, operation]) => ({ reason, label, operation }));
}

export function isPage(value, requestedPage, focused = false) {
  return Boolean(value && Array.isArray(value.items) && value.items.length <= BALANCE_PAGE_SIZE
    && value.page_size === BALANCE_PAGE_SIZE && Number.isInteger(value.page) && value.page >= 1
    && Number.isInteger(value.total_pages) && value.total_pages >= 1 && value.page <= value.total_pages
    // The server clamps a stale page to the last remaining page, including
    // after an all-to-own permission change. Other page mismatches stay invalid.
    && (focused || value.page === Math.min(requestedPage, value.total_pages))
    && Number.isInteger(value.total) && value.total >= value.items.length);
}

export function validContext(value) {
  return Boolean(value?.actor?.id && typeof value.session_scope === 'string' && value.session_scope
    && Array.isArray(value.resources) && Array.isArray(value.operations)
    && value.resources.every(resource => typeof resource.kind === 'string' && resource.id && typeof resource.name === 'string' && resource.access));
}
