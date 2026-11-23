import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { readBalanceFreshness } from './balanceFreshnessApi';
import { balanceSnapshot } from './visibleBalanceFreshness';
import { canReadOperationalPage } from './visibleOperationalRead';
import BalanceFreshnessNotice from './BalanceFreshnessNotice';

// This owner notice only reads lightweight saved health. It cannot refresh
// finances or keep the balances workspace's financial request alive.
export default function GlobalBalanceFreshness() {
  const { user, loading, authStatus } = useAuth();
  const location = useLocation();
  const workspace = ['/operational-balances', '/operational-balances/daily-movements'].includes(location.pathname);
  const enabled = !loading && authStatus === 'authenticated' && user?.is_owner === true && Boolean(user.id) && !workspace;
  const identity = JSON.stringify([user?.id, user?.owner_id, user?.created_by, user?.tenant_id, authStatus]);
  const [generation, setGeneration] = useState(0), [result, setResult] = useState(null);
  const current = useRef(null), saved = useRef(null), lastState = useRef(null);
  current.current = { identity, enabled };
  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true, controller = null, pending = false;
    const valid = () => alive && current.current.enabled && current.current.identity === identity && canReadOperationalPage();
    const update = value => { lastState.current = { key: identity, value }; setResult(lastState.current); };
    const read = async () => {
      if (!valid() || pending) return;
      pending = true;
      const request = controller = new AbortController();
      const previous = saved.current?.key === identity ? saved.current.value : null;
      const previousMessage = lastState.current?.key === identity ? lastState.current.value.message : '';
      update({ phase: 'checking', snapshot: previous, message: previousMessage });
      try {
        const value = await readBalanceFreshness({ signal: request.signal });
        if (!valid() || request.signal.aborted) return;
        const snapshot = balanceSnapshot(value, previous?.session_scope, user.id);
        if (previous && (snapshot.asOf < previous.asOf || snapshot.revision < previous.revision)) throw new Error('older_balance_health');
        saved.current = { key: identity, value: snapshot };
        update({ phase: snapshot.stale ? 'stale' : 'fresh', snapshot, message: '' });
      } catch (error) {
        if (!valid() || request.signal.aborted) return;
        if ([401, 403].includes(error?.response?.status)) {
          saved.current = null;
          update({ phase: 'denied', snapshot: null, message: '' });
          return;
        }
        // Do not dismiss a known warning because a health read failed (including
        // a 404 during version mismatch). No successful read means unverified.
        update({ phase: 'error', snapshot: previous,
          message: 'تعذر التحقق من تحديث المصادر الآن. تبقى حالة التحديث السابقة غير محسومة حتى تأكيد التعافي.' });
      } finally { pending = false; }
    };
    const activity = () => {
      if (!alive || current.current.identity !== identity || !current.current.enabled) return;
      if (!canReadOperationalPage()) {
        controller?.abort();
        if (pending) update({ phase: 'error', snapshot: saved.current?.key === identity ? saved.current.value : null,
          message: 'توقفت قراءة حالة التحديث قبل التأكيد. أعد التحقق عندما تكون الصفحة ظاهرة والاتصال متاحاً.' });
      }
      else read();
    };
    document.addEventListener('visibilitychange', activity);
    window.addEventListener('online', activity);
    window.addEventListener('offline', activity);
    read();
    return () => {
      alive = false; controller?.abort();
      document.removeEventListener('visibilitychange', activity);
      window.removeEventListener('online', activity);
      window.removeEventListener('offline', activity);
    };
  }, [enabled, identity, user?.id, location.key, generation]);
  const check = useCallback(() => setGeneration(value => value + 1), []);
  if (!enabled || result?.key !== identity) return null;
  return <BalanceFreshnessNotice state={result.value} compact globalNotice
    onCheck={check} href="/operational-balances" />;
}
