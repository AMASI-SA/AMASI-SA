import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ProviderStatementAccessDialog from './ProviderStatementAccessDialog';
import { useOptionalAuth } from '../context/AuthContext';
import { getProviderStatementAccess, saveProviderStatementAccess } from '../services/providerStatementAccess';

jest.mock('../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('../services/providerStatementAccess', () => ({ getProviderStatementAccess: jest.fn(), saveProviderStatementAccess: jest.fn() }));
const employee = { id: 'employee', name: 'هلال', account: { user_id: 'staff' } };
const current = { employee_id: 'employee', user: { id: 'staff', name: 'هلال' },
    access: { mode: 'none', providers: [], policy_version: 3, page_revision: 5, session_scope: 'a'.repeat(64), enabled: false },
    providers: [{ id: 'tabby', name: 'تابي' }, { id: 'tamara', name: 'تمارا' }, { id: 'salla', name: 'سلة' }] };
const saved = (mode = 'write', providers = ['tabby']) => ({ ...current,
    access: { ...current.access, mode, providers, enabled: true, policy_version: 4, page_revision: 6 } });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
let host, root;
beforeEach(() => {
    jest.clearAllMocks(); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    useOptionalAuth.mockReturnValue({ user: { id: 'owner', role: 'owner' }, authStatus: 'authenticated' });
    getProviderStatementAccess.mockResolvedValue(current);
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount()); host.remove(); delete document.visibilityState;
    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
});
async function render(props = {}) {
    const onSaved = jest.fn();
    await act(async () => root.render(<ProviderStatementAccessDialog employee={employee} onSaved={onSaved} onClose={jest.fn()} {...props} />));
    return onSaved;
}
async function mode(value) { await act(async () => { const el = host.querySelector('select'); el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); }); }
async function pick() { await act(async () => host.querySelector('input[aria-label="تابي"]').click()); }
async function submit() { await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); }

test('saves only selected platforms and observed revisions, with no financial confirmation option', async () => {
    saveProviderStatementAccess.mockResolvedValue(saved());
    const onSaved = await render();
    expect(getProviderStatementAccess).toHaveBeenCalledWith('employee', expect.any(AbortSignal));
    expect(host.querySelectorAll('select option').length).toBe(3);
    await mode('write'); expect(host.querySelector('button[type="submit"]').disabled).toBe(true);
    await pick(); await submit();
    expect(saveProviderStatementAccess).toHaveBeenCalledTimes(1);
    expect(saveProviderStatementAccess).toHaveBeenCalledWith('employee', {
        mode: 'write', providers: ['tabby'], expected_policy_version: 3,
        expected_page_revision: 5, expected_session_scope: 'a'.repeat(64),
    }, expect.any(AbortSignal));
    expect(onSaved).toHaveBeenCalledTimes(1);
});

test('removing access clears all selected providers in the saved grant', async () => {
    getProviderStatementAccess.mockResolvedValue({ ...current, access: { ...current.access, mode: 'write', providers: ['tabby'] } });
    saveProviderStatementAccess.mockResolvedValue(saved('none', []));
    await render(); await mode('none'); await submit();
    expect(saveProviderStatementAccess.mock.calls[0][1]).toMatchObject({ mode: 'none', providers: [] });
});

test.each([409, 503])('uncertain/conflicting %s save retains draft and requires explicit fresh read', async status => {
    saveProviderStatementAccess.mockRejectedValue({ response: { status } });
    const onSaved = await render(); await mode('write'); await pick(); await submit();
    expect(host.querySelector('select').value).toBe('write');
    expect(host.querySelector('input[aria-label="تابي"]').checked).toBe(true);
    expect(host.querySelector('button[type="submit"]').disabled).toBe(true);
    await submit(); expect(saveProviderStatementAccess).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
});

test.each([
    { ...current, user: { id: 'other' } },
    { ...current, access: { ...current.access, providers: ['tabby', 'tabby'] } },
    { ...current, providers: [{ id: 'unknown', name: 'مجهول' }] },
])('invalid linked-account or platform contract never enables the form', async result => {
    getProviderStatementAccess.mockResolvedValue(result);
    await render(); expect(host.querySelector('form')).toBeNull();
    expect(saveProviderStatementAccess).not.toHaveBeenCalled();
});

test('a success response with a different platform or session is not accepted', async () => {
    saveProviderStatementAccess.mockResolvedValue(saved('write', ['salla']));
    const onSaved = await render(); await mode('write'); await pick(); await submit();
    expect(onSaved).not.toHaveBeenCalled(); expect(host.textContent).toContain('تعذّر تأكيد الحفظ');
});

test('switching authenticated owner aborts and ignores the old private grant response', async () => {
    const old = deferred(); getProviderStatementAccess.mockReturnValueOnce(old.promise);
    await render(); const oldSignal = getProviderStatementAccess.mock.calls[0][1];
    useOptionalAuth.mockReturnValue({ user: { id: 'new-owner', role: 'owner' }, authStatus: 'authenticated' });
    getProviderStatementAccess.mockRejectedValue({ response: { status: 403 } });
    await render(); await act(async () => old.resolve(current));
    expect(oldSignal.aborted).toBe(true); expect(host.querySelector('form')).toBeNull();
});

test('hidden mount performs no request; hidden in-flight save cannot report success', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await render(); expect(getProviderStatementAccess).not.toHaveBeenCalled();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => [...host.querySelectorAll('button')].find(el => el.textContent === 'إعادة تحميل الصلاحيات').click());
    const pending = deferred(); saveProviderStatementAccess.mockReturnValueOnce(pending.promise);
    const onSaved = await render(); await mode('write'); await pick(); await submit();
    const signal = saveProviderStatementAccess.mock.calls[0][2];
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(saved()));
    expect(onSaved).not.toHaveBeenCalled(); expect(host.querySelector('button[type="submit"]').disabled).toBe(true);
});

test.each([
    { loading: true, authStatus: 'authenticated' },
    { loading: false, authStatus: 'checking' },
    { loading: false, authStatus: 'recoverable_error' },
])('unverified current authentication %j cannot read or display a private grant', async state => {
    useOptionalAuth.mockReturnValue({ user: { id: 'owner', role: 'owner' }, ...state });
    await render();
    expect(getProviderStatementAccess).not.toHaveBeenCalled();
    expect(host.querySelector('form')).toBeNull();
});

test('authentication checking aborts an in-flight save and fences its success', async () => {
    const pending = deferred(); saveProviderStatementAccess.mockReturnValueOnce(pending.promise);
    const onSaved = await render(); await mode('write'); await pick(); await submit();
    const signal = saveProviderStatementAccess.mock.calls[0][2];
    useOptionalAuth.mockReturnValue({ user: { id: 'owner', role: 'owner' }, loading: true, authStatus: 'authenticated' });
    await render({ onSaved });
    expect(signal.aborted).toBe(true); expect(host.querySelector('form')).toBeNull();
    await act(async () => pending.resolve(saved()));
    expect(onSaved).not.toHaveBeenCalled();
});

test('changed employee account aborts private reads and rejects the prior linked identity', async () => {
    const pending = deferred(); getProviderStatementAccess.mockReturnValueOnce(pending.promise);
    await render(); const signal = getProviderStatementAccess.mock.calls[0][1];
    await render({ employee: { ...employee, account: { user_id: 'replacement-account' } } });
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(current));
    expect(host.querySelector('form')).toBeNull();
    expect(saveProviderStatementAccess).not.toHaveBeenCalled();
});

test('a new explicit read after an uncertain save uses the current revisions and does not repeat the old grant', async () => {
    saveProviderStatementAccess.mockRejectedValueOnce({ response: { status: 503 } });
    await render(); await mode('write'); await pick(); await submit();
    getProviderStatementAccess.mockResolvedValue(saved());
    await act(async () => [...host.querySelectorAll('button')].find(el => el.textContent === 'إعادة تحميل الصلاحيات').click());
    expect(saveProviderStatementAccess).toHaveBeenCalledTimes(1);
    expect(host.querySelector('select').value).toBe('write');
    await mode('none');
    saveProviderStatementAccess.mockResolvedValue({ ...saved('none', []), access: { ...saved('none', []).access, policy_version: 5, page_revision: 7 } });
    await submit();
    expect(saveProviderStatementAccess.mock.calls[1][1]).toMatchObject({ mode: 'none', providers: [], expected_policy_version: 4, expected_page_revision: 6 });
});
