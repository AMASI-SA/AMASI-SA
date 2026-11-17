// Entirely synthetic HTTP fixtures for browser acceptance. No production data.
const operations = [
  ['employee.payment', 'صرف مستحق الموظف'], ['employee.accrual', 'إثبات مستحق للموظف'],
  ['supplier.payment', 'سداد المورد'], ['external.pay', 'دفع مبلغ للجهة'],
  ['external.receive', 'استلام تسوية مديونية'], ['external.salary', 'صرف راتب موظف من الجهة'],
  ['external.household', 'مصروفات منزلية'], ['external.personal', 'مصروفات شخصية'],
  ['custody.spend', 'الدفع من العهدة'], ['accounts.transfer', 'تحويل بين الحسابات'],
  ['customer.refund_request', 'طلب استرجاع مبلغ'], ['customer.refund_execute', 'تسجيل رد المبلغ'],
].map(([key, label]) => ({ key, label }));
const resource = (kind, id, name, amount, side, permitted = []) => ({ kind, id, name, currency: 'SAR',
  balance: { amount, side, visible: true }, access: { use: true, movements: 'all', balance: true }, operations: permitted });
const resources = [
  resource('external_person', 'external-demo', 'جهة خارجية توضيحية', '400.00', 'for_party', ['external.pay', 'external.receive', 'external.salary', 'external.household', 'external.personal']),
  resource('employee', 'employee-demo', 'موظف توضيحي', '1000.00', 'for_party', ['employee.accrual', 'employee.payment']),
  resource('supplier', 'supplier-demo', 'مورد توضيحي', '2000.00', 'for_party', ['supplier.payment']),
  resource('supplier', 'supplier-extra', 'مورد إضافي للاختبار', '500.00', 'for_party', ['supplier.payment']),
  resource('employee_custody', 'custody-demo', 'عهدة الموظف التوضيحي', '500.00', 'for_us', []),
  resource('owner_withdrawal', 'owner-demo', 'حساب المالك التوضيحي', '0.00', 'balanced', []),
  resource('bank', 'bank-demo', 'بنك المؤسسة التوضيحي', '5000.00', 'for_us', ['accounts.transfer']),
  resource('cash', 'cash-demo', 'صندوق المؤسسة التوضيحي', '500.00', 'for_us', ['accounts.transfer']),
];
const scope = 'b'.repeat(64);
const owner = { id: 'synthetic-owner', name: 'مالك الاختبار', role: 'owner', is_owner: true };
const staff = { id: 'synthetic-staff', name: 'موظف الاختبار', role: 'employee', is_owner: false, owner_id: owner.id };
const context = actor => ({ actor, session_scope: scope, permission_version: 3, status: 'active',
  capabilities: { create_entries: true, manage_access: actor.is_owner }, operations, resources });
const ledger = Array.from({ length: 7 }, (_, index) => ({ id: `movement-${index + 1}`, event_id: `event-${index + 1}`,
  description: index % 2 ? 'دفعة مسجلة للجهة — توضيحي' : 'مبلغ مسجل لحساب الجهة — توضيحي',
  business_date: `2026-11-${String(17 - index).padStart(2, '0')}`, recorded_at: '2026-11-17T10:00:00Z',
  recorded_by: { id: staff.id, name: staff.name }, amount: `${(index + 1) * 50}.00`, currency: 'SAR',
  side: index % 2 ? 'for_us' : 'for_party', reason: index % 2 ? 'payment' : 'receipt', order_number: null,
  party_type: 'external_person', party_id: 'external-demo', party_name: resources[0].name,
}));
const order = { id: 'order-demo', order_number: '2044', total: '300.00', currency: 'SAR', paid_amount: '300.00',
  refunded_amount: '0.00', reserved_amount: '0.00', available_amount: '300.00', can_request: true,
  methods: [{ id: 'bank', label: 'تحويل بنكي' }, { id: 'original', label: 'تمارا' }],
  banks: [{ id: 'bank-demo', name: 'بنك المؤسسة التوضيحي', currency: 'SAR' }] };
const refund = index => ({ id: `refund-${index}`, order_number: String(2044 + index), amount: '50.00', currency: 'SAR',
  method: 'bank', source: { kind: 'bank', id: 'bank-demo', name: 'بنك المؤسسة التوضيحي' }, status: 'pending',
  reason: 'استرجاع توضيحي', request_reference: '', recorded_by: { id: staff.id, name: staff.name },
  created_at: '2026-11-17T10:00:00Z', can_confirm: true, can_cancel: true, can_reconcile: false, provider_evidence: [] });
const platformRefund = { ...refund('platform'), order_number: '2055', method: 'original',
  source: { kind: 'provider', id: 'tamara', name: 'تمارا' }, can_confirm: false, can_cancel: false, can_reconcile: true,
  provider_evidence: [{ id: 'refund:tamara:synthetic-proof', reference: 'SYNTHETIC-PROOF', amount: '50.00', fingerprint: 'c'.repeat(64) }] };
const access = { version: 3, enabled: true, operations: ['external.pay', 'custody.spend'], resources: [
  { kind: 'bank', id: 'bank-demo', use: true, movements: 'own', balance: false },
  { kind: 'external_person', id: 'external-demo', use: true, movements: 'own', balance: true },
] };
const catalog = { operations, resources: resources.map(({ kind, id, name, currency }) => ({ kind, id, name, currency })),
  resource_kinds: [['external_person', 'الجهات الخارجية'], ['employee', 'الموظفون'], ['supplier', 'الموردون'],
    ['employee_custody', 'العهد'], ['owner_withdrawal', 'المالك'], ['bank', 'البنوك'], ['cash', 'الصناديق']].map(([key, label]) => ({ key, label })) };
module.exports = { synthetic_only: true, operations, resources, scope, owner, staff, context, ledger, order, refund, platformRefund, access, catalog };
