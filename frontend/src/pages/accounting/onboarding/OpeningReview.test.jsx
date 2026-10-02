import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import OpeningReview from './OpeningReview';
let root, node;
beforeEach(() => {global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement('div'); document.body.appendChild(node); root = createRoot(node);});
afterEach(() => {act(() => root.unmount()); node.remove(); delete global.IS_REACT_ACT_ENVIRONMENT;});
const entry = {line_no: 1, label: 'حساب موثق', entity_type: 'asset', entity_id: 'exact-bank', sub_account: 'bank', category: 'financial_account', side: 'debit', sar_amount: '15.00', original_amount: '1.234', original_currency: 'KWD', evidence_file_id: 'file-1', fx_snapshot: {rate_to_sar: '12.15559', fx_at: '2026-10-02T21:00:00Z', source: 'owner-approved-rate'}};
test('shows actual canonical journal legs, original precision, fingerprints and equity difference without inferring owner acceptance from balance', () => {
    const session = {id: 'snapshot-id', version: 9, status: 'reviewed', reviewed_hash: 'hash-123', reviewed_by: 'reviewer-id', preview: {hash: 'hash-123', balanced: true, debit_total: '15.00', credit_total: '15.00', entries: [entry, {...entry, line_no: 2, entity_id: 'opening_balance_equity', category: 'opening_equity_counterpart', side: 'credit'}], evidence: [{source_file_id: 'file-1', purpose: 'opening_balance', section_id: 'banks_cash', owner_id: 'owner-id', sha256: 'a'.repeat(64), size: 17}]}};
    act(() => root.render(<OpeningReview session={session} />));
    for (const value of ['snapshot-id', 'hash-123', 'reviewer-id', 'exact-bank', '1.234 KWD', 'owner-approved-rate', 'a'.repeat(64), 'اعتماد المالك التجاري: غير مثبت', 'توازن القيد وحده لا يثبت']) expect(node.textContent).toContain(value);
    const cells = [...node.querySelectorAll('dl div')];
    expect(cells.find(e => e.textContent.includes('الفرق قبل')).textContent).toContain('15.00');
    expect(cells.find(e => e.textContent.includes('الفرق النهائي')).textContent).toContain('0.00');
    expect(node.querySelectorAll('button')).toHaveLength(0);
    act(() => root.render(<OpeningReview session={session} dirty />));
    expect(node.querySelector('[role="alert"]').textContent).toContain('لقطة سابقة');
    expect(node.textContent).toContain('مراجعة الصلاحية غير مثبتة');
});
test('no preview or missing values never become a zero or a passed approval', () => {
    act(() => root.render(<OpeningReview session={{id: 'draft', version: 1}} />));
    expect(node.textContent).toContain('لم تُنشأ معاينة محفوظة');
    expect(node.textContent).not.toContain('0.00');
    expect(node.querySelectorAll('button')).toHaveLength(0);
});
