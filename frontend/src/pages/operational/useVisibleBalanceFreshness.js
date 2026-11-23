import { useCallback, useEffect, useRef, useState } from 'react';
import { balanceFreshnessManager } from './balanceFreshnessApi';
import { canReadOperationalPage } from './visibleOperationalRead';

// Some ledger/detail dialogs belong to lazy children rather than the workspace
// router. Their open state must fence the financial request too.
const canUseBalances = () => canReadOperationalPage()
  && !document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]');

export function useVisibleBalanceFreshness({ context, identity, canRefresh, onConfirmed,
  manager = balanceFreshnessManager }) {
  const actor = context?.actor?.id, scope = context?.session_scope;
  const owner = context?.actor?.is_owner === true && Boolean(actor && scope);
  const uiKey = JSON.stringify([identity, actor, scope, context?.permission_version, context?.status]);
  const latest = useRef(null);
  latest.current = { uiKey, owner, canRefresh: Boolean(canRefresh && context?.status === 'active'), onConfirmed };
  const handle = useRef(null);
  const [result, setResult] = useState(null);
  useEffect(() => {
    if (!owner) return undefined;
    let alive = true;
    const refreshAllowed = latest.current.canRefresh;
    const current = () => alive && latest.current.uiKey === uiKey && latest.current.owner
      && (!refreshAllowed || latest.current.canRefresh) && canUseBalances();
    const close = () => { handle.current?.close(); handle.current = null; };
    const open = () => {
      if (!current()) { close(); return; }
      if (handle.current) return;
      const consumer = manager.attach({ actor, scope, canRefresh: latest.current.canRefresh,
        isCurrent: current,
        onChange: value => { if (current()) setResult({ key: uiKey, value }); },
        onConfirmed: snapshot => { if (current() && latest.current.canRefresh) latest.current.onConfirmed?.(snapshot); },
      });
      handle.current = consumer;
      consumer.check(false);
    };
    const activity = () => canUseBalances() ? open() : close();
    const dialogs = new MutationObserver(activity);
    dialogs.observe(document.body, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['open', 'role', 'aria-modal'] });
    document.addEventListener('visibilitychange', activity);
    window.addEventListener('online', activity);
    window.addEventListener('offline', activity);
    open();
    return () => {
      alive = false; close();
      dialogs.disconnect();
      document.removeEventListener('visibilitychange', activity);
      window.removeEventListener('online', activity);
      window.removeEventListener('offline', activity);
    };
  }, [actor, scope, owner, uiKey, canRefresh, manager]);
  const retry = useCallback(() => {
    if (latest.current.owner && latest.current.canRefresh && canUseBalances()) handle.current?.check(true);
  }, []);
  const state = result?.key === uiKey ? result.value : { phase: 'idle', snapshot: null, message: '' };
  return { ...state, owner, retry, canRefresh: Boolean(canRefresh && context?.status === 'active') };
}
