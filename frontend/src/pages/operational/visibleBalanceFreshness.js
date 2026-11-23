// Only calculation time can prove freshness. A report generation or manual
// movement revision does not advance source reconciliation.
export const BALANCE_FRESHNESS_MS = 2 * 60 * 60 * 1000;
export const BALANCE_RETRY_MS = 90000;
const healthStates = new Set(['current', 'stale', 'failed', 'updating', 'unknown', 'inactive']);
const optionalTime = value => value === null || calculationTime(value) !== null;
const riyadhDay = value => new Date(value + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);

export function calculationTime(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

export function balanceSnapshot(value, scope, actor, observedAt = Date.now()) {
  if (!value || value.schema_version !== 1 || value.actor?.is_owner !== true
      || typeof value.actor.id !== 'string' || actor && value.actor.id !== actor
      || typeof value.session_scope !== 'string' || !value.session_scope || scope && value.session_scope !== scope
      || !Number.isInteger(value.revision) || value.revision < 0
      || !['active', 'draft', 'frozen', 'accounting_active', 'unknown'].includes(value.status)
      || !healthStates.has(value.freshness?.state) || value.freshness.max_age_seconds !== 7200
      || !Array.isArray(value.sources) || !Array.isArray(value.issues)
      || value.sources.some(source => !source || typeof source.key !== 'string' || typeof source.label !== 'string'
        || !healthStates.has(source.state) || typeof source.message !== 'string' || !optionalTime(source.last_success_at))
      || value.issues.some(issue => !issue || typeof issue.code !== 'string' || typeof issue.source !== 'string'
        || !['warning', 'error', 'info'].includes(issue.severity) || typeof issue.message !== 'string' || !optionalTime(issue.last_success_at))
      || value.last_attempt !== null && (!value.last_attempt || calculationTime(value.last_attempt.started_at) === null
        || !optionalTime(value.last_attempt.finished_at) || !['running', 'success', 'failed', 'unknown'].includes(value.last_attempt.outcome))) throw new Error('invalid_balance_freshness');
  const asOf = calculationTime(value.as_of);
  const reference = calculationTime(value.generated_at);
  if (reference === null || value.as_of !== null && asOf === null || asOf !== null && asOf > reference
      || value.last_attempt && calculationTime(value.last_attempt.started_at) > reference) throw new Error('invalid_balance_calculation_time');
  return { ...value, asOf, reference, observedAt, stale: value.status === 'active'
    && (asOf === null || reference - asOf >= BALANCE_FRESHNESS_MS || riyadhDay(reference) !== riyadhDay(asOf)) };
}

// A one-shot local display deadline, never a source refresh schedule. Pair the
// server reference clock with response-arrival time to tolerate client skew.
export function balanceExpiryDelay(snapshot, clock = Date.now()) {
  if (!snapshot || snapshot.status !== 'active' || snapshot.stale || snapshot.asOf === null) return null;
  const riyadhMidnight = Date.parse(riyadhDay(snapshot.reference) + 'T21:00:00Z');
  const deadline = Math.min(snapshot.asOf + BALANCE_FRESHNESS_MS, riyadhMidnight);
  return Math.max(0, deadline - snapshot.reference - Math.max(0, clock - snapshot.observedAt));
}

const unknownMessage = 'لم تتأكد نتيجة تحديث المصادر. قد يكون الطلب ما زال جارياً؛ تظهر آخر أرصدة محفوظة. أعد التحقق قبل المحاولة مجدداً.';
const readMessage = 'تعذر التحقق من حداثة المصادر. تظهر آخر أرصدة محفوظة؛ لم يثبت تعافي التحديث التلقائي.';

// One financial lane belongs to actor + server scope, independently of React
// mounts and permission-version changes. Observers never own a financial write.
// This is deliberately memory-only; every new page load still reads the server
// before a POST, and the server lease remains the cross-tab/process authority.
export function createBalanceFreshnessManager({ read, refresh, now = Date.now }) {
  const records = new Map();
  const recordFor = (actor, scope) => {
    const key = JSON.stringify([actor, scope]);
    if (!records.has(key)) records.set(key, {
      actor, scope, clients: new Set(), flight: null, attempt: null, snapshot: null,
      state: { phase: 'idle', snapshot: null, message: '' },
    });
    return records.get(key);
  };
  const eligible = client => !client.closed && client.isCurrent();
  const allowed = record => [...record.clients].some(client => client.canRefresh && eligible(client));
  const publish = (record, patch) => {
    record.state = { ...record.state, ...patch };
    for (const client of record.clients) if (eligible(client)) client.onChange(record.state);
  };
  const confirm = (record, snapshot) => {
    record.attempt = null;
    publish(record, { phase: snapshot.stale ? 'stale' : 'fresh', snapshot, message: '' });
    for (const client of record.clients) if (client.canRefresh && eligible(client)) client.onConfirmed(snapshot);
  };
  const remember = (record, snapshot) => {
    const previous = record.snapshot;
    // Never let a delayed/cache response roll the displayed calculation back.
    if (previous && (snapshot.asOf < previous.asOf || snapshot.revision < previous.revision)) throw new Error('stale_balance_freshness');
    record.snapshot = snapshot;
    publish(record, { snapshot });
  };
  const run = (record, client, explicit) => {
    if (!eligible(client)) return Promise.resolve();
    if (record.flight) return record.flight.promise;
    const controller = new AbortController();
    const flight = { controller, stage: 'read', promise: null, ownsPost: false };
    record.flight = flight;
    const active = () => !controller.signal.aborted && [...record.clients].some(eligible);
    const current = () => {
      if (!active()) throw new DOMException('Page is not active', 'AbortError');
    };
    const readSnapshot = async () => {
      const value = await read({ signal: controller.signal });
      current();
      const snapshot = balanceSnapshot(value, record.scope, record.actor, now());
      remember(record, snapshot);
      return snapshot;
    };
    publish(record, { phase: 'checking', message: record.state.message });
    flight.promise = Promise.resolve().then(async () => {
      current();
      const snapshot = await readSnapshot();
      if (snapshot.status !== 'active') {
        publish(record, { phase: snapshot.status === 'unknown' ? 'unknown' : 'inactive',
          message: snapshot.status === 'unknown' ? readMessage : 'احتساب الأرصدة التلقائي غير نشط في الحالة الحالية.' });
        return;
      }
      if (snapshot.issues.some(issue => issue.code === 'operational_health_unavailable')) {
        publish(record, { phase: 'unknown', message: 'لم يكتمل التحقق من سجل محاولات التحديث. أعد التحقق من الحالة قبل إرسال تحديث جديد.' });
        return;
      }
      if (snapshot.asOf === null) {
        publish(record, { phase: 'unknown', message: 'لا يوجد تاريخ احتساب محفوظ يمكن التحقق منه. راجع حالة المصادر قبل إعادة الاحتساب.' });
        return;
      }
      const durable = snapshot.last_attempt;
      if (durable?.outcome === 'running' || snapshot.freshness.state === 'updating') {
        publish(record, { phase: 'unknown', message: 'يوجد تحديث مصادر جارٍ. تظهر الأرصدة المحفوظة حتى تتأكد النتيجة؛ لم تُرسل محاولة أخرى.' });
        return;
      }
      if (!record.attempt && ['failed', 'unknown'].includes(durable?.outcome)) record.attempt = {
        asOf: snapshot.asOf, revision: snapshot.revision,
        startedAt: now() - Math.max(0, snapshot.reference - calculationTime(durable.started_at)),
      };
      const attempt = record.attempt;
      if (attempt && snapshot.asOf > attempt.asOf && !snapshot.stale) {
        confirm(record, snapshot);
        return;
      }
      if (!snapshot.stale && !explicit) {
        publish(record, { phase: attempt ? 'unknown' : 'fresh', message: attempt ? unknownMessage : '' });
        return;
      }
      if (!client.canRefresh || !eligible(client) || !allowed(record)) {
        publish(record, { phase: attempt ? 'unknown' : snapshot.stale ? 'stale' : 'fresh', message: attempt ? unknownMessage : '' });
        return;
      }
      // Re-entry may observe a committed result, but never retries an ambiguous
      // financial attempt. Only a new, explicit intent may retry after a fresh
      // server read and the minimum server deadline + cleanup allowance.
      if (attempt && (!explicit || now() - attempt.startedAt < BALANCE_RETRY_MS)) {
        publish(record, { phase: 'unknown', message: unknownMessage });
        return;
      }
      current();
      if (!eligible(client) || !client.canRefresh) return;
      const pending = { asOf: snapshot.asOf, revision: snapshot.revision, startedAt: now() };
      record.attempt = pending; // Persist before dispatch, including cancellation.
      flight.stage = 'refresh'; flight.ownsPost = true;
      publish(record, { phase: 'refreshing', message: '' });
      const receipt = await refresh({ signal: controller.signal, expectedSessionScope: record.scope });
      current();
      if (!allowed(record)) throw new DOMException('Workspace is not active', 'AbortError');
      const receiptTime = calculationTime(receipt?.as_of);
      if (receipt?.status !== 'refreshed' || receipt.session_scope !== record.scope || receiptTime === null
          || receiptTime <= pending.asOf || !Number.isInteger(receipt.revision) || receipt.revision < pending.revision) {
        publish(record, { phase: 'unknown', message: receipt?.refresh_warning || unknownMessage });
        return;
      }
      // A receipt alone does not authorize a green indicator or clear an alert.
      // Re-read saved metadata once; there is no timer, wait or polling loop.
      flight.stage = 'confirm';
      const confirmed = await readSnapshot();
      if (confirmed.status !== 'active' || confirmed.stale || confirmed.asOf < receiptTime
          || confirmed.revision < receipt.revision) throw new Error('unconfirmed_balance_refresh');
      confirm(record, confirmed);
    }).catch(() => {
      const ambiguous = Boolean(record.attempt);
      publish(record, { phase: ambiguous ? 'unknown' : 'error',
        message: ambiguous ? unknownMessage : readMessage });
    }).finally(() => {
      if (record.flight === flight) record.flight = null;
    });
    return flight.promise;
  };
  return {
    attach({ actor, scope, canRefresh = false, isCurrent = () => true, onChange, onConfirmed = () => {} }) {
      const record = recordFor(actor, scope);
      const client = { canRefresh, isCurrent, onChange, onConfirmed, closed: false };
      record.clients.add(client);
      if (eligible(client)) onChange(record.state);
      return {
        check: (explicit = false) => run(record, client, explicit),
        close() {
          client.closed = true;
          record.clients.delete(client);
          if (record.flight && ((!record.flight.ownsPost && ![...record.clients].some(eligible))
              || record.flight.ownsPost && !allowed(record))) {
            // A read-only global observer must not keep a workspace POST alive.
            record.flight.controller.abort();
            if (record.attempt) publish(record, { phase: 'unknown', message: unknownMessage });
          }
        },
      };
    },
  };
}
