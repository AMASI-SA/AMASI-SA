import { useEffect, useRef, useState } from 'react';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { visibleOperationalRead } from './visibleOperationalRead';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const code = failure => failure?.response?.data?.detail?.code;
const missing = failure => failure?.response?.status === 404 && code(failure) === 'operational_pending_command_not_found';
const retired = failure => failure?.response?.status === 410 && code(failure) === 'operational_pending_command_retired';
function notApplied(failure, record) {
  if (failure?.response?.data?.detail?.not_applied === true) return true;
  const command = record?.command;
  const payout = record?.action === 'entry' && command.side === 'for_us' && command.source_id && (!command.reason || command.reason === 'payment');
  return !payout && [400, 422].includes(failure?.response?.status);
}

function readMarker(storageKey) {
  try {
    const id = localStorage.getItem(storageKey);
    if (id && !uuid.test(id)) throw new Error('invalid recovery marker');
    return { id };
  } catch {
    return { id: null, error: 'تعذر قراءة مرجع العملية السابقة. راجع السجل قبل إدخال عملية أخرى.' };
  }
}

export async function pendingBalanceStorageKey(identity) {
  const bytes = new TextEncoder().encode(identity);
  const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return `mezan.pending-command.v1:${[...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('')}`;
}

// Persistent browser storage contains only an opaque UUID. The authenticated
// server owns the immutable command; a mount only reads it and never executes it.
export function usePendingBalanceCommand({ context, storageIdentity, validate, execute, validateSaved, onSaved, onRetired }) {
  const binding = JSON.stringify([context.actor.id, context.session_scope, context.permission_version, storageIdentity]);
  const currentBinding = useRef(binding); currentBinding.current = binding;
  const callbacks = useRef({ validate, execute, validateSaved, onSaved, onRetired });
  callbacks.current = { validate, execute, validateSaved, onSaved, onRetired };
  const keyRef = useRef(null), idRef = useRef(null), recordRef = useRef(null), confirmedRef = useRef(false);
  const mounted = useRef(false), lock = useRef(null), completed = useRef(false), rejected = useRef(false), lane = useRef({ pending: null });
  const [record, setRecord] = useState(null);
  const [recovering, setRecovering] = useState(true);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState('');

  function checked(value, id, validator = callbacks.current.validate, scope = context.session_scope) {
    if (value?.id !== id || !value.command || value.command.request_id !== id
      || value.command.expected_session_scope !== scope || !validator(value)) {
      throw { localMessage: 'لم تطابق العملية المستعادة هذا الحساب. راجع السجل قبل المتابعة.' };
    }
    return value;
  }
  function publish(value, confirmed = true) {
    recordRef.current = value; confirmedRef.current = confirmed;
    setRecord(value ? { binding, value } : null);
  }
  function removeMarker(id, storageKey) {
    if (localStorage.getItem(storageKey) === id) localStorage.removeItem(storageKey);
    if (keyRef.current?.key === storageKey && idRef.current === id) idRef.current = null;
  }
  async function retireRecord(id, storageKey) {
    const result = await api.retirePending(id, context.session_scope);
    if (result?.deleted !== true || result.id !== id) throw new Error('retirement unconfirmed');
    removeMarker(id, storageKey);
  }
  function retiredRecord(id, storageKey) {
    removeMarker(id, storageKey); recordRef.current = null; confirmedRef.current = false;
    setRecord(null); setBlocked(true);
    setError('انتهت متابعة العملية السابقة. راجع السجل المحدث قبل إدخال عملية جديدة.');
    callbacks.current.onRetired?.();
  }

  function resolveEntry(value, decision, storageKey) {
    if (value.action !== 'entry' || !decision || decision.status === 'pending') return false;
    if (decision.status === 'saved') {
      const result = decision.result;
      if (result?.saved !== true || !callbacks.current.validateSaved(result, value)) {
        throw { localMessage: 'تعذر مطابقة إيصال الحركة بهذا الحساب. أعد التحقق من النتيجة.' };
      }
      completed.current = true;
      // A storage error cannot invalidate a confirmed receipt.
      try { removeMarker(value.id, storageKey); } catch { /* the UUID can only recover this receipt */ }
      publish(null, false);
      callbacks.current.onSaved(result, { recovered: true, business_date: value.command.business_date });
    } else if (decision.status === 'discarded') {
      removeMarker(value.id, storageKey); publish(null, false);
      setNotice('أُلغي الطلب السابق قبل تنفيذه. راجع تفاصيل الحركة قبل تأكيدها.');
    } else if (decision.status === 'rejected' && typeof decision.detail?.message === 'string') {
      removeMarker(value.id, storageKey); publish(null, false);
      setError(decision.detail.message);
    } else {
      throw { localMessage: 'لم تتأكد نتيجة العملية السابقة. أعد التحقق منها قبل تسجيل حركة أخرى.' };
    }
    return true;
  }

  useEffect(() => {
    mounted.current = true;
    keyRef.current = null; idRef.current = null; recordRef.current = null; confirmedRef.current = false;
    completed.current = false; rejected.current = false; lock.current = null; setBusy(false);
    setRecord(null); setBlocked(false); setError(''); setNotice(''); setRecovering(true);
    let disposed = false, stop;
    const active = () => !disposed && mounted.current && currentBinding.current === binding;
    pendingBalanceStorageKey(storageIdentity).then(storageKey => {
      if (!active()) return;
      keyRef.current = { binding, key: storageKey };
      const marker = readMarker(storageKey);
      idRef.current = marker.id;
      setBlocked(Boolean(marker.error)); setError(marker.error || '');
      if (!marker.id || marker.error) { setRecovering(false); return; }
      stop = visibleOperationalRead(config => api.pendingCommand(marker.id, context.session_scope, config), {
      onData: value => {
        if (!active()) return;
        try {
          const recovered = checked(value, marker.id);
          if (!resolveEntry(recovered, recovered.resolution, storageKey)) publish(recovered);
        }
        catch (failure) { setBlocked(true); setError(simpleBalanceError(failure)); }
      },
      onError: failure => {
        if (!active()) return;
        if (missing(failure)) {
          // Keep the same UUID: a delayed registration cannot become a second
          // financial intent if the user has to re-enter an unregistered form.
          setError('لم يصل حفظ بيانات العملية إلى الخادم. أعد إدخالها؛ سيُستخدم مرجع العملية نفسه.');
        } else if (retired(failure)) retiredRecord(marker.id, storageKey);
        else { setBlocked(true); setError(simpleBalanceError(failure)); }
      },
      onEnd: () => { if (active()) setRecovering(false); },
      }, lane.current);
    }).catch(() => {
      if (active()) { setRecovering(false); setBlocked(true); setError('تعذر تجهيز استعادة العملية بأمان. أعد فتح الصفحة قبل الحفظ.'); }
    });
    return () => { disposed = true; mounted.current = false; stop?.(); };
    // Binding changes intentionally discard in-memory data and reauthorize GET.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding, revision]);

  async function discard() {
    const value = recordRef.current, storageKey = keyRef.current?.key;
    if (!value || value.action !== 'entry' || value.resolution?.status !== 'pending' || idRef.current !== value.id
      || keyRef.current?.binding !== binding || lock.current || recovering || blocked || completed.current) return;
    const attempt = { binding }, active = () => mounted.current && currentBinding.current === binding;
    lock.current = attempt; setBusy(true); setError(''); setNotice('');
    try {
      if (localStorage.getItem(storageKey) !== value.id) throw {localMessage:'تغير مرجع العملية. أعد التحقق قبل المتابعة.'};
      const result = await api.discardPending(value.id, context.session_scope);
      if (!active()) return;
      if (result?.id !== value.id) throw {localMessage:'تعذر تأكيد هوية العملية. أعد التحقق من نتيجتها.'};
      if (!resolveEntry(value, result, storageKey)) {
        throw {localMessage:'لم يُؤكّد إلغاء الطلب. أعد التحقق من النتيجة.'};
      }
    } catch (failure) {
      if (active()) { setBlocked(true); setError(simpleBalanceError(failure)); }
    } finally {
      if (lock.current === attempt) { lock.current = null; if (active()) setBusy(false); }
    }
  }

  async function submit(action, targetId, makeCommand) {
    if (lock.current || completed.current || recovering || blocked || keyRef.current?.binding !== binding) return;
    const storageKey = keyRef.current.key;
    const startedBinding = binding;
    const operationCallbacks = callbacks.current;
    const checkOperation = (value, id) => checked(value, id, operationCallbacks.validate, context.session_scope);
    const attempt = { binding: startedBinding };
    const active = () => mounted.current && currentBinding.current === startedBinding;
    lock.current = attempt; setBusy(true); setError(''); setNotice('');
    let phase = 'validate';
    try {
      if (rejected.current && idRef.current) {
        await retireRecord(idRef.current, storageKey);
        if (active()) { rejected.current = false; publish(null, false); }
        return;
      }
      let value = recordRef.current;
      const newCommand = !value;
      if (!value) {
        if (localStorage.getItem(storageKey) !== idRef.current) {
          setBlocked(true);
          throw {localMessage:'تغير مرجع العملية في نافذة أخرى. أعد التحقق من العملية المحفوظة.'};
        }
        const body = makeCommand();
        const id = idRef.current || globalThis.crypto.randomUUID();
        if (!uuid.test(id)) throw { localMessage: 'تعذر إنشاء مرجع آمن للعملية. أعد فتح الصفحة.' };
        value = { id, action, target_id: targetId || null,
          command: { ...body, request_id: id, expected_session_scope: context.session_scope } };
        checkOperation(value, id);
        // This is deliberately a UUID string, never the financial command.
        try { localStorage.setItem(storageKey, id); }
        catch { throw { localMessage: 'تعذر حفظ مرجع العملية لإعادة المحاولة. تحقق من إعدادات التخزين ثم أعد المحاولة.' }; }
        idRef.current = id; publish(value, false);
      }
      if (!confirmedRef.current) {
        phase = 'register';
        if (!newCommand) {
          try {
            const found = checkOperation(await api.pendingCommand(value.id, context.session_scope), value.id);
            if (!active() || resolveEntry(found, found.resolution, storageKey)) return;
          }
          catch (failure) { if (!missing(failure)) throw failure; }
        }
        const registered = await api.registerPending(value.id, {
          action: value.action, target_id: value.target_id, command: value.command,
        });
        value = checkOperation(registered, value.id);
        if (active()) {
          if (resolveEntry(value, value.resolution, storageKey)) return;
          publish(value);
        }
      }
      // Navigation during registration leaves a recoverable intent, not a
      // background financial write. A fresh mount must read and explicitly retry.
      if (!active()) return;
      if (localStorage.getItem(storageKey) !== value.id) {
        setBlocked(true);
        throw { localMessage: 'تغير مرجع العملية في نافذة أخرى. أعد استعادة العملية قبل المتابعة.' };
      }
      phase = 'execute';
      const result = await operationCallbacks.execute(value);
      if (result?.saved !== true || !operationCallbacks.validateSaved(result, value)) throw { localMessage: 'لم تتأكد نتيجة الحفظ. أعد محاولة العملية نفسها للتحقق دون تكرارها.' };
      if (active()) completed.current = true;
      // Failure to retire recovery data must not disguise a confirmed save. The
      // retained UUID can only recover/replay that same financial operation.
      try { await retireRecord(value.id, storageKey); } catch { /* retained for safe recovery */ }
      if (active()) { setRecord(null); recordRef.current = null; operationCallbacks.onSaved(result); }
    } catch (failure) {
      if (!active()) return;
      if (retired(failure) && idRef.current) retiredRecord(idRef.current, storageKey);
      else if (phase === 'register' && code(failure) === 'operational_pending_command_conflict') {
        // Never overwrite the existing server command or execute a different
        // form under its UUID. Restore it for review first.
        setBlocked(true); setError('هذا المرجع مرتبط بعملية محفوظة. استعد بياناتها وراجعها قبل إعادة المحاولة.');
      } else if (phase === 'execute' && notApplied(failure, recordRef.current)) {
        rejected.current = true;
        try {
          await retireRecord(idRef.current, storageKey);
          if (active()) { rejected.current = false; publish(null, false); }
        } catch { /* keep the marker and exact command until retirement succeeds */ }
        if (active()) setError(simpleBalanceError(failure));
      } else {
        if (phase === 'register' && [400, 422].includes(failure?.response?.status)) publish(null, false);
        setError(simpleBalanceError(failure));
      }
    } finally {
      if (lock.current === attempt) {
        lock.current = null;
        if (active()) setBusy(false);
      }
    }
  }
  return { pending: record?.binding === binding ? record.value : null, recovering, busy, blocked, error, notice, submit, discard,
    recover: () => { if (!lock.current && !busy) setRevision(value => value + 1); }, clearError: () => { if (!blocked) setError(''); } };
}
