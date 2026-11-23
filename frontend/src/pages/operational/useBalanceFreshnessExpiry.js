import { useEffect, useState } from 'react';
import { balanceExpiryDelay } from './visibleBalanceFreshness';

export function useBalanceFreshnessExpiry(state) {
  const snapshot = state.snapshot;
  const key = snapshot ? JSON.stringify([snapshot.actor.id, snapshot.session_scope,
    snapshot.as_of, snapshot.revision, snapshot.generated_at, snapshot.observedAt]) : '';
  const [expired, setExpired] = useState('');
  useEffect(() => {
    let timer = null, alive = true;
    const clear = () => { if (timer !== null) clearTimeout(timer); timer = null; };
    const check = () => {
      clear();
      if (!alive || document.visibilityState === 'hidden') return;
      const remaining = balanceExpiryDelay(snapshot);
      if (remaining === null) return;
      if (remaining <= 0) setExpired(key);
      else timer = setTimeout(() => { timer = null; if (alive && document.visibilityState !== 'hidden') setExpired(key); }, remaining);
    };
    check();
    document.addEventListener('visibilitychange', check);
    return () => { alive = false; clear(); document.removeEventListener('visibilitychange', check); };
  }, [key, snapshot]);
  if (!key || expired !== key || snapshot.stale) return state;
  return { ...state, phase: ['checking', 'refreshing'].includes(state.phase) ? state.phase : 'stale',
    snapshot: { ...snapshot, stale: true, freshness: { ...snapshot.freshness,
      state: snapshot.freshness.state === 'current' ? 'stale' : snapshot.freshness.state } },
    message: state.message || 'مرّ موعد تحديث الأرصدة أو بدأ يوم جديد في الرياض. لم يُؤكد احتساب أحدث؛ أعد التحقق من المصادر.' };
}
