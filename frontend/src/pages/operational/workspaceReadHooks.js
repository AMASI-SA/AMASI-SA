import { useCallback, useState } from 'react';
import { useReadDemand } from '../../lib/demandRead';
import { useBalanceRead } from './simpleBalancesHooks';

export function useWorkspaceRead(read, key, enabled = true, policy) {
  const [revision, setRevision] = useState(0);
  const query = JSON.stringify([key, revision]);
  const demand = useReadDemand(query, enabled);
  const result = useBalanceRead(config => read({ ...config, demand: true, readDemand: demand }), query, enabled, policy);
  // A manual retry is a new consumer. A late DELETE from the previous attempt
  // must not revoke this retry's freshly renewed interest in the same result.
  const reload = useCallback(() => setRevision(value => value + 1), []);
  return { ...result, reload };
}

export function sameWorkspace(value, context) {
  return value?.session_scope === context.session_scope
    && value.permission_version === context.permission_version;
}

export function assertWorkspace(value, context) {
  if (!sameWorkspace(value, context)) throw {
    response: { status: 409, data: { detail: { message: 'تغيرت جلسة الحساب أو صلاحياته. أعد قراءة الصفحة للتحقق من المتاح لك.' } } },
  };
}

export function retryWorkspaceRead(read, context) {
  if ([401, 403, 409].includes(read.status) && context.reloadWorkspace) context.reloadWorkspace();
  else read.reload();
}

export function validResourcePage(value, context, pageSize = 24) {
  return sameWorkspace(value, context) && Array.isArray(value.items) && value.items.length <= pageSize
    && Number.isInteger(value.total) && value.total >= 0 && value.page_size === pageSize
    && Number.isInteger(value.page) && Number.isInteger(value.pages) && value.pages >= 1
    && value.page >= 1 && value.page <= value.pages
    && value.items.every(item => typeof item.kind === 'string' && item.id && typeof item.name === 'string' && item.access);
}
