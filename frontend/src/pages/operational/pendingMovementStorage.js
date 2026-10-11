// A financial request survives reload until the server gives a definitive result.
// The scope comes from the authenticated actor AND tenant, never from form data.
export const pendingMovementKey = scope => `mezan.operational.pending-movement.v1:${encodeURIComponent(scope)}`;
export function readPendingMovement(scope) {
  if (!scope) throw new Error('movement_storage_scope_missing');
  const raw = window.localStorage.getItem(pendingMovementKey(scope));
  if (!raw) return null;
  const item = JSON.parse(raw);
  if (item.version !== 1 || item.scope !== scope || typeof item.payload?.request_id !== 'string' || !item.payload?.party_type || typeof item.payload?.amount !== 'string') throw new Error('movement_storage_invalid');
  return item.payload;
}
export function savePendingMovement(scope,payload) {
  if (!scope) throw new Error('movement_storage_scope_missing');
  window.localStorage.setItem(pendingMovementKey(scope),JSON.stringify({version:1,scope,payload}));
}
export function clearPendingMovement(scope) {
  if (!scope) throw new Error('movement_storage_scope_missing');
  window.localStorage.removeItem(pendingMovementKey(scope));
}
