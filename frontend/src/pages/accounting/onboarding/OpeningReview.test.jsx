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

test('successive snapshots preserve every leg when a zero fact makes the equity line number repeat', () => {
    const bank = {...entry, line_no: 1, original_amount: '20.00', original_currency: 'SAR', sar_amount: '20.00', fx_snapshot: {rate_to_sar: '1', fx_at: '2026-10-02T21:00:00Z', source: 'sar_parity'}};
    const payable = {...bank, line_no: 3, label: 'مورد موثق', entity_type: 'supplier', entity_id: 'supplier-id', sub_account: 'main', category: 'supplier_payable', side: 'credit', original_amount: '5.00', sar_amount: '5.00'};
    // The server retains source line numbers after omitting a zero fact, then
    // numbers the counterpart by the count of nonzero entries: both are 3.
    const equity = {...payable, line_no: 3, label: 'حقوق الملكية', entity_type: 'equity', entity_id: 'opening_balance_equity', category: 'opening_equity_counterpart', original_amount: '15.00', sar_amount: '15.00'};
    const snapshot = (version, hash, entries) => ({id: 'snapshot-id', version, preview: {hash, debit_total: '20.00', credit_total: '20.00', zero_accounts: [{entity_type: 'asset', entity_id: 'zero-cash', sub_account: 'cash'}], entries}});
    const first = snapshot(1, 'snapshot-hash-1', [bank, payable, equity]);
    const second = snapshot(2, 'snapshot-hash-2', [{...bank, line_no: 2}, payable, equity]);
    const original = JSON.stringify([first, second]);
    const errors = jest.spyOn(console, 'error').mockImplementation(() => {});
    const rows = () => [...node.querySelectorAll('table[aria-label="أطراف القيد"] tbody tr')];
    try {
        act(() => root.render(<OpeningReview session={first} />));
        expect(rows()).toHaveLength(3);
        act(() => root.render(<OpeningReview session={second} />));
        expect(rows()).toHaveLength(3);
        for (const id of ['exact-bank', 'supplier-id', 'opening_balance_equity']) {
            expect(rows().filter(row => row.cells[0].textContent.includes(id))).toHaveLength(1);
        }
        expect(rows().map(row => [row.cells[4].textContent, row.cells[5].textContent])).toEqual([['20.00', '—'], ['—', '5.00'], ['—', '15.00']]);
        expect(rows().some(row => row.textContent.includes('zero-cash'))).toBe(false);
        act(() => root.render(<OpeningReview session={second} />));
        expect(rows()).toHaveLength(3);
        expect(errors).not.toHaveBeenCalled();
        expect(JSON.stringify([first, second])).toBe(original);
    } finally {
        errors.mockRestore();
    }
});

test('canonical legs with a repeated line number keep their rows on reorder and clear on a new empty snapshot', () => {
    const bank = {...entry, line_no: 3};
    const equity = {...entry, line_no: 3, label: 'حقوق الملكية', entity_type: 'equity', entity_id: 'opening_balance_equity', sub_account: 'main', category: 'opening_equity_counterpart', side: 'credit'};
    const session = {id: 'snapshot-id', version: 1, preview: {hash: 'same-hash', entries: [bank, equity]}};
    const rows = () => [...node.querySelectorAll('table[aria-label="أطراف القيد"] tbody tr')];
    const errors = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
        act(() => root.render(<OpeningReview session={session} />));
        const [bankRow, equityRow] = rows();
        act(() => root.render(<OpeningReview session={{...session, preview: {...session.preview, entries: [equity, bank]}}} />));
        expect(rows()).toHaveLength(2);
        expect(rows()[0]).toBe(equityRow);
        expect(rows()[1]).toBe(bankRow);
        act(() => root.render(<OpeningReview session={{...session, version: 2, preview: {hash: 'zero-only-hash', entries: []}}} />));
        expect(rows()).toHaveLength(0);
        expect(node.textContent).toContain('لا توجد أطراف قيد في اللقطة المستلمة');
        expect(errors).not.toHaveBeenCalled();
    } finally {
        errors.mockRestore();
    }
});
