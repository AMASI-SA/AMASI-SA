import { useEffect, useRef, useState } from 'react';
import { formatRiyadhDateTime } from '../../lib/dates';
import { operationalApi as api, messageFor, requestId } from './api';
import { formatPositionMoney } from './financialPositionPresentation';

export default function CustomerWalletBalance({ sessionScope, onSaved }) {
  const [opened, setOpened] = useState(false);
  const [context, setContext] = useState(null);
  const [form, setForm] = useState({ amount: '', asOf: '', reference: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const active = useRef(true);
  const lock = useRef(false);
  const pending = useRef(null);
  const scope = useRef(sessionScope);
  scope.current = sessionScope;

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  useEffect(() => {
    if (!opened) return undefined;
    let current = true;
    setContext(null);
    setError('');
    api.customerWalletContext().then(result => {
      if (!current) return;
      if (result.session_scope !== sessionScope) throw Error('تغيرت الجلسة. أعد فتح التقرير.');
      setContext(result);
    }).catch(failure => {
      if (current) setError(failure.response ? messageFor(failure) : failure.message);
    });
    return () => { current = false; };
  }, [opened, sessionScope]);

  const edit = patch => {
    if (busy) return;
    pending.current = null;
    setForm(value => ({ ...value, ...patch }));
    setSaved(false);
  };

  const save = async event => {
    event.preventDefault();
    if (lock.current || !context?.can_manage || context.session_scope !== sessionScope) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setSaved(false);
    const submittedScope = sessionScope;
    try {
      const command = pending.current || {
        request_id: requestId(), expected_session_scope: sessionScope,
        merchant_id: context.merchant_id, currency: 'SAR',
        as_of: `${form.asOf}+03:00`, remaining_balance: form.amount,
        source_reference: form.reference,
      };
      pending.current = command;
      const result = await api.saveCustomerWalletSnapshot(command);
      if (!active.current || scope.current !== submittedScope) return;
      if (result.session_scope !== submittedScope) throw Error('تعذر التحقق من الجلسة بعد الحفظ. أعد تحميل التقرير.');
      pending.current = null;
      setSaved(true);
      onSaved?.();
    } catch (failure) {
      if (active.current && scope.current === submittedScope) {
        setError(failure.response ? messageFor(failure) : failure.message || 'تعذر حفظ رصيد المحفظة.');
      }
    } finally {
      if (active.current && scope.current === submittedScope) {
        lock.current = false;
        setBusy(false);
      }
    }
  };

  const balances = Object.entries(context?.balances || {});
  return <section className="op-card ofp-no-print" aria-label="توثيق رصيد محافظ العملاء">
    <button type="button" className="ofp-button" onClick={() => setOpened(value => !value)} disabled={busy}>
      {opened ? 'إغلاق تحديث محافظ العملاء' : 'تحديث رصيد محافظ العملاء من كشف سلة'}
    </button>
    {opened && <div>
      <h3>رصيد محافظ العملاء المستحق علينا</h3>
      <p>أدخل الرصيد المتبقي في كشف محافظ العملاء بسلة وتاريخ الكشف. يحل الرصيد الجديد محل الرصيد السابق في إجمالي الالتزامات.</p>
      {balances.map(([currency, balance]) => <p key={currency}>
        الرصيد الموثق {formatPositionMoney(balance.remaining)} {currency} · حتى {formatRiyadhDateTime(balance.as_of)}
      </p>)}
      {!context && !error && <p role="status">جارٍ قراءة بيانات المتجر…</p>}
      {context && !context.can_manage && <p>تحديث كشف المحافظ متاح لمالك المتجر أثناء التشغيل.</p>}
      {context?.can_manage && <form onSubmit={save} className="op-form">
        <label className="op-field">الرصيد المتبقي للعملاء بالريال
          <input aria-label="الرصيد المتبقي للعملاء بالريال" type="number" min="0" step="0.01" required
            value={form.amount} disabled={busy} onChange={event => edit({ amount: event.target.value })} />
        </label>
        <label className="op-field">تاريخ كشف سلة بتوقيت الرياض
          <input aria-label="تاريخ كشف سلة بتوقيت الرياض" type="datetime-local" step="1" required
            value={form.asOf} disabled={busy} onChange={event => edit({ asOf: event.target.value })} />
        </label>
        <label className="op-field">مرجع كشف سلة
          <input aria-label="مرجع كشف سلة" type="text" minLength="3" maxLength="300" required
            value={form.reference} disabled={busy} onChange={event => edit({ reference: event.target.value })} />
        </label>
        <button type="submit" className="ofp-button" disabled={busy}>{busy ? 'جارٍ حفظ الرصيد…' : 'حفظ رصيد محافظ العملاء'}</button>
      </form>}
      {error && <p role="alert">{error}</p>}
      {saved && <p role="status">حُفظ الرصيد ضمن المبالغ المستحقة للعملاء علينا.</p>}
    </div>}
  </section>;
}
