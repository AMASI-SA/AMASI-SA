import React, { useEffect, useRef, useState } from 'react';
import { requestId } from './api';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { Amount } from './ApprovedFinanceUI';

export default function MovementReversal({ row, context, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  const command = useRef(null);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  if (row.reverse_of) return <p>عكس الحركة <bdi>{row.reverse_of}</bdi></p>;
  if (row.reversal_id) return <p role="status">تم عكس الحركة · مرجع العكس <bdi>{row.reversal_id}</bdi></p>;
  if (row.can_reverse !== true || context.status !== 'active' || !context.session_scope) return null;

  async function confirm(event) {
    event.preventDefault();
    if (locked.current || !reason.trim()) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      command.current ??= { request_id:requestId(), expected_session_scope:context.session_scope, reason:reason.trim() };
      const result = await api.reverseMovement(row.id, command.current);
      if (typeof result?.id !== 'string' || !result.id || result.reverse_of !== row.id) {
        throw { localMessage:'تعذر التحقق من مرجع العكس. راجع الكشف أو أعد المحاولة بنفس المرجع.' };
      }
      if (active.current) onSaved(result);
    } catch (failure) {
      if (active.current) setError(simpleBalanceError(failure));
    } finally {
      locked.current = false;
      if (active.current) setBusy(false);
    }
  }

  if (!editing) return <button type="button" className="button" onClick={() => setEditing(true)}>عكس الحركة</button>;
  return <form className="form" onSubmit={confirm}>
    <p>سيُسجَّل قيد عكسي بقيمة <Amount value={row.amount} currency={row.currency} /> بتاريخ اليوم.</p>
    <label className="field">سبب العكس
      <textarea required maxLength={500} value={reason} disabled={busy || Boolean(command.current)}
        onChange={event => setReason(event.target.value)} />
    </label>
    {error && <p role="alert">{error}</p>}
    <button type="submit" className="button primary" disabled={busy || !reason.trim()}>
      {busy ? 'جارٍ تسجيل العكس…' : command.current ? 'إعادة محاولة العكس' : 'تأكيد العكس'}
    </button>
    {!command.current && <button type="button" className="button" onClick={() => setEditing(false)}>إلغاء</button>}
  </form>;
}
