import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import BalanceFreshnessNotice from './BalanceFreshnessNotice';

const LAST = '2026-11-21T14:50:00Z';
const sourceMessage = 'توقف تحديث مصدر سناب بعد انتهاء صلاحية الاتصال.';
const issueMessage = 'يلزم التحقق من اكتمال بيانات الشحن.';
const snapshot = {
  actor: { id: 'owner', is_owner: true }, session_scope: 'scope', status: 'active',
  as_of: LAST, asOf: Date.parse(LAST), revision: 10, generated_at: '2026-11-23T14:00:00Z', stale: true,
  freshness: { state: 'failed', max_age_seconds: 7200 },
  last_attempt: { started_at: '2026-11-23T13:00:00Z', finished_at: '2026-11-23T13:01:00Z', outcome: 'failed', error_code: 'operational_refresh_failed' },
  sources: [
    { key: 'snapchat', label: 'إعلانات Snapchat', state: 'failed', last_success_at: LAST,
      data_through: '2026-11-20', expected_through: '2026-11-22', message: sourceMessage },
    { key: 'shipping', label: 'شركات الشحن', state: 'unknown', last_success_at: LAST, message: 'لم يثبت تحديث الشحن.' },
  ],
  issues: [
    { source: 'snapchat', code: 'operational_source_failed', severity: 'error', last_success_at: LAST, message: sourceMessage },
    { source: 'snapchat', code: 'source_connection_required', severity: 'error', last_success_at: LAST, message: sourceMessage },
    { source: 'shipping', code: 'shipping_incomplete', severity: 'warning', last_success_at: LAST, message: issueMessage },
    { source: 'shipping', code: 'shipping_evidence_missing', severity: 'warning', last_success_at: LAST, message: issueMessage },
  ],
};
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const render = async props => act(async () => root.render(<BalanceFreshnessNotice compact globalNotice
  state={{ phase: 'fresh', snapshot, message: '' }} {...props} />));
const collapsedText = () => { const view = host.cloneNode(true); view.querySelectorAll('details').forEach(node => node.remove()); return view.textContent; };

test('compact notice keeps the saved time, useful outcome and family names outside closed details', async () => {
  await render();
  const text = collapsedText();
  expect(text).toContain('تحديث الأرصدة يحتاج إلى متابعة');
  expect(text).toContain('آخر احتساب محفوظ');
  expect(host.querySelector('time').dateTime).toBe(LAST);
  expect(text).toContain('فشلت آخر محاولة');
  expect(text).toContain('إعلانات Snapchat'); expect(text).toContain('شركات الشحن');
  expect(text).not.toContain(sourceMessage); expect(text).not.toContain(issueMessage);
  expect(text).not.toContain('operational_'); expect(text).not.toContain('shipping_incomplete');
  expect(host.querySelector('details').open).toBe(false);
  expect(host.querySelector('details > summary').textContent).toBe('تفاصيل المصادر والمحاولات');
});
test('native disclosure expands full details; duplicate source and issue messages appear once', async () => {
  await render();
  const details = host.querySelector('details');
  await act(async () => details.querySelector('summary').click());
  expect(details.open).toBe(true);
  const text = details.textContent;
  expect(text.split(sourceMessage)).toHaveLength(2); expect(text.split(issueMessage)).toHaveLength(2);
  expect(text).toContain('آخر نجاح'); expect(text).toContain('2026-11-20');
  for (const code of ['operational_refresh_failed', 'operational_source_failed', 'source_connection_required', 'shipping_incomplete', 'shipping_evidence_missing']) expect(text).toContain(code);
  expect(details.querySelector('[role="region"]').tabIndex).toBe(0);
  await act(async () => details.querySelector('summary').click()); expect(details.open).toBe(false);
});
test('deduplication preserves a distinct last-success date while null adds no duplicate source message', async () => {
  const newer = '2026-11-22T14:50:00Z';
  await render({ state: { phase: 'fresh', message: '', snapshot: { ...snapshot, sources: [snapshot.sources[0]],
    issues: [
      { source: 'snapchat', code: 'source_old', message: sourceMessage, last_success_at: LAST },
      { source: 'snapchat', code: 'source_no_date', message: sourceMessage, last_success_at: null },
      { source: 'snapchat', code: 'source_new', message: sourceMessage, last_success_at: newer },
      { source: 'snapchat', code: 'source_new_duplicate', message: sourceMessage, last_success_at: newer },
    ],
  } } });
  const details = host.querySelector('details');
  await act(async () => details.querySelector('summary').click());
  expect(details.textContent.split(sourceMessage)).toHaveLength(3);
  const format = value => new Intl.DateTimeFormat('ar-SA', { timeZone: 'Asia/Riyadh', calendar: 'gregory', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
  expect(details.textContent).toContain(format(LAST)); expect(details.textContent).toContain(format(newer));
  for (const code of ['source_old', 'source_no_date', 'source_new', 'source_new_duplicate']) expect(details.textContent).toContain(code);
});
test('opening support details never invokes the status check or workspace update callback', async () => {
  const check = jest.fn(); await render({ onCheck: check });
  await act(async () => host.querySelector('summary').click()); expect(check).not.toHaveBeenCalled();
  const button = host.querySelector('button'); expect(button.textContent).toBe('التحقق من الحالة');
  await act(async () => button.click()); expect(check).toHaveBeenCalledTimes(1);
});
test('workspace update action remains explicit while raw error codes stay inside details', async () => {
  const update = jest.fn(); await render({ compact: false, canUpdate: true, onCheck: update });
  expect(collapsedText()).not.toContain('operational_refresh_failed');
  expect(host.querySelector('button').textContent).toBe('تحديث المصادر');
  await act(async () => host.querySelector('button').click()); expect(update).toHaveBeenCalledTimes(1);
});
test('unknown issue identifiers use a human family label rather than exposing keys in normal flow', async () => {
  await render({ state: { phase: 'unknown', message: '', snapshot: { ...snapshot, sources: [], last_attempt: null,
    issues: [{ source: 'internal_reader_v12', code: 'internal_timeout_v2', message: 'تعذر التحقق من المصدر.', severity: 'warning', last_success_at: null }] } } });
  expect(collapsedText()).toContain('مصادر أخرى');
  expect(collapsedText()).not.toContain('internal_');
  expect(host.querySelector('details').textContent).toContain('internal_timeout_v2');
});
