import { useCallback, useEffect, useRef, useState } from 'react';
import { visibleOperationalRead } from './visibleOperationalRead';
import { simpleBalanceError } from './simpleBalancesApi';

// The visible result belongs to one actor and one query. A cancelled old read
// may finish physically, but cannot disclose its data in the next account.
export function useBalanceRead(read, key, enabled = true, { singleAttemptTimeoutMs, deferUntilVisible = true } = {}) {
  const reader = useRef(read);
  reader.current = read;
  const lane = useRef({ pending: null });
  const sequence = useRef(0);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState(null);
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(null);
  useEffect(() => {
    const current = ++sequence.current;
    const request = reader.current;
    setResult(null); setFailure(null); setBusy(null);
    if (!enabled) return undefined;
    const stop = visibleOperationalRead(request, {
      onStart: () => { if (current === sequence.current) setBusy(key); },
      onData: value => { if (current === sequence.current) setResult({ key, value }); },
      onError: error => {
        if (current === sequence.current) setFailure({ key, status: error?.response?.status,
          message: error?.code === 'OPERATIONAL_VISIBLE_READ_TIMEOUT'
            ? 'انتهت مهلة قراءة البيانات. أعد المحاولة.' : simpleBalanceError(error) });
      },
      onPaused: () => {
        if (current === sequence.current) setFailure({ key,
          message: 'توقفت قراءة البيانات. أعد المحاولة عندما تكون الصفحة ظاهرة والاتصال متاحًا.' });
      },
      onEnd: () => { if (current === sequence.current) setBusy(null); },
    }, lane.current, { singleAttemptTimeoutMs, deferUntilVisible });
    return () => { sequence.current += 1; stop(); };
  }, [key, enabled, revision, singleAttemptTimeoutMs, deferUntilVisible]);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  return {
    data: enabled && result?.key === key ? result.value : null,
    error: enabled && failure?.key === key ? failure.message : '',
    status: enabled && failure?.key === key ? failure.status : null,
    loading: enabled && busy === key,
    reload,
  };
}

export function authIdentity(auth) {
  const user = auth?.user;
  return JSON.stringify([user?.id, user?.owner_id, user?.created_by, user?.merchant_id,
    user?.tenant_id, user?.role, user?.is_owner, auth?.authStatus]);
}
