// Synthetic acceptance data for the new display/report/review workflows.
const f = require('./fixtures.cjs');
const employee = f.resources.find(row => row.kind === 'employee');
const salary = (day, balance) => ({ id: `salary:employee-demo:2026-11-${day}`, event_id: `salary-${day}`,
  description: 'راتب تلقائي', business_date: `2026-11-${day}`, recorded_at: `2026-11-${day}T00:00:00Z`,
  amount: '100.00', currency: 'SAR', side: 'for_party', reason: 'salary', recorded_by: null,
  party_type: employee.kind, party_id: employee.id, party_name: employee.name,
  automatic_history: 'salary', automatic_group: `salary-${day}`, history_count: 2, balance_after: balance });
const employeeFull = [
  { ...salary('01', '1900.00'), id: 'opening-employee', reason: 'opening', amount: '1900.00',
    description: 'رصيد افتتاحي', automatic_history: null, automatic_group: null },
  salary('18', '2000.00'),
  { ...salary('18', '1900.00'), id: 'employee-payment', reason: 'payment', amount: '100.00',
    description: 'دفعة راتب', recorded_at: '2026-11-18T12:00:00Z', side: 'for_us',
    note: 'دفعة من مستحق الشهر',
    recorded_by: { id: f.staff.id, name: f.staff.name }, automatic_history: null, automatic_group: null },
  salary('19', '2000.00'),
];
const employeeLedger = [employeeFull[3], employeeFull[2], employeeFull[0]];
const employeeHistory = { resource: { ...employee, balance: { amount: '2000.00', side: 'for_party', visible: true } },
  automatic_history: 'salary', current_month: '2026-11', items: [employeeFull[3], employeeFull[1]],
  page: 1, page_size: 5, total: 2, total_pages: 1, history_count: 2, current_month_total: '200.00', previous_months: [] };
const reviews = Array.from({ length: 6 }, (_, index) => ({ assignment_id: `review-demo-${index + 1}`,
  driver_id: index % 2 ? 'driver-extra' : 'employee-demo', driver_name: index % 2 ? 'موصل إضافي للاختبار' : 'موصل توضيحي',
  customer_name: `عميل توضيحي ${index + 1}`, order_number: `300${index + 1}`, amount: '300.00', currency: 'SAR',
  payment_method: 'bank_transfer', bank_account_id: 'bank-demo', bank_name_snapshot: 'بنك المؤسسة التوضيحي',
  status: 'pending', has_receipt: true, receipt_reference: `synthetic-receipt-${index + 1}`,
  submitted_at: `2026-11-19T${String(12 - index).padStart(2, '0')}:00:00Z` }));
function statement(resource, allRows, query, actor) {
  const positive = ['bank', 'cash', 'employee_custody'].includes(resource.kind) ? 'for_us' : 'for_party';
  const ordered = [...allRows].sort((a, b) => `${a.business_date}:${a.recorded_at}:${a.id}`.localeCompare(`${b.business_date}:${b.recorded_at}:${b.id}`));
  let running = resource.kind === 'employee' ? 0 : 20000;
  let opening = running, closing = running;
  const items = [], totals = { for_party: 0, for_us: 0 };
  for (const row of ordered) {
    const amount = Math.round(Number(row.amount) * 100);
    const delta = row.side === positive ? amount : -amount;
    running += delta;
    if (row.business_date < query.date_from || row.reason === 'opening') opening += delta;
    else if (row.business_date <= query.date_to) {
      totals[row.side] += amount;
      items.push({ ...row, balance_after: (running / 100).toFixed(2) });
    }
    if (row.business_date <= query.date_to) closing = running;
  }
  return { resource, items, opening_balance: (opening / 100).toFixed(2), closing_balance: (closing / 100).toFixed(2),
    totals: Object.fromEntries(Object.entries(totals).map(([side, value]) => [side, (value / 100).toFixed(2)])),
    date_from: query.date_from, date_to: query.date_to, currency: 'SAR', scope: 'all', can_print: true,
    prepared_by: { id: actor.id, name: actor.name }, generated_at: '2026-11-19T12:00:00Z', balance_status: 'available', balance_note: '' };
}
module.exports = { employeeFull, employeeLedger, employeeHistory, reviews, statement };
