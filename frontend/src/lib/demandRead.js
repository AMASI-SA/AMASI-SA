import { useEffect, useMemo } from 'react';
import http from './api';

const visible = () => document.visibilityState !== 'hidden' && document.hidden !== true && navigator.onLine !== false;
const aborted = () => new DOMException('Read is no longer visible', 'AbortError');
const consumerId = () => globalThis.crypto?.randomUUID?.()
  || `read-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
const releasePath = value => typeof value === 'string'
  && /^\/(?:operational-balances\/workspace|dashboard-v2|preparation-work-v1\/assembly)\/read-demand\/[^/?#]+$/.test(value);

function createAttempt(id) {
  let claim = null, active = true;
  const release = () => {
    if (!claim) return;
    const current = claim;
    claim = null;
    Promise.resolve().then(() => http.delete(current, {
      params: { consumer_id: id }, timeout: 5000,
    })).catch(() => {});
  };
  return {
    consumerId: id,
    get active() { return active; },
    claim(path) {
      if (!releasePath(path)) throw new Error('Invalid read demand release path');
      if (claim && claim !== path) release();
      claim = path;
      if (!active || !visible()) release();
    },
    release,
    dispose() { active = false; release(); },
  };
}

// A mounted query owns its attempts. Each resumed attempt gets a new consumer
// so a delayed release from its predecessor cannot cancel the new lease.
export function createReadDemand() {
  let active = true, latestId = consumerId(), first = true;
  const attempts = new Set();
  const release = () => { for (const attempt of attempts) attempt.dispose(); };
  return {
    get consumerId() { return latestId; },
    get active() { return active; },
    activate() { active = true; },
    begin() {
      if (!first) latestId = consumerId();
      first = false;
      const attempt = createAttempt(latestId);
      attempts.add(attempt);
      if (!active) attempt.dispose();
      return attempt;
    },
    finish(attempt) { attempt.dispose(); attempts.delete(attempt); },
    release,
    dispose() { active = false; release(); },
  };
}

export function useReadDemand(key, enabled = true) {
  const demand = useMemo(() => createReadDemand(), [key]);
  useEffect(() => {
    demand.activate();
    if (!enabled) { demand.dispose(); return undefined; }
    const pause = () => { if (!visible()) demand.release(); };
    document.addEventListener('visibilitychange', pause);
    window.addEventListener('offline', pause);
    return () => {
      demand.dispose();
      document.removeEventListener('visibilitychange', pause);
      window.removeEventListener('offline', pause);
    };
  }, [demand, enabled]);
  return demand;
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(aborted()); return; }
    const cancel = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(aborted()); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve(); }, ms);
    signal?.addEventListener('abort', cancel, { once: true });
  });
}

// request returns the HTTP response, not only its body. Only an explicit 202
// preparation response can poll; a completed or failed read never refreshes.
export async function demandRead(request, config = {}, options = {}) {
  const { readDemand, ...network } = config;
  const owner = readDemand || createReadDemand();
  const demand = owner.begin();
  const signal = network.signal;
  let polling = false;
  const release = () => demand.release();
  signal?.addEventListener('abort', release, { once: true });
  const assertActive = () => {
    if (signal?.aborted || !demand.active || !visible()) throw aborted();
  };
  try {
    for (;;) {
      assertActive();
      const response = await request({ ...network,
        headers: { ...network.headers, 'X-Mezan-Read-Consumer': demand.consumerId, ...(polling && { 'X-Mezan-Read-Poll': '1' }) },
      });
      const value = response?.data;
      if (response?.status === 202) {
        if (value?.status !== 'preparing' || typeof value.key !== 'string' || !value.key
          || !Number.isFinite(value.retry_after_ms) || value.retry_after_ms < 0) {
          throw new Error('Invalid read preparation response');
        }
        demand.claim(value.release_url || `${options.releasePath || '/operational-balances/workspace/read-demand'}/${encodeURIComponent(value.key)}`);
        polling = true;
        assertActive();
        await wait(Math.min(10000, Math.max(250, value.retry_after_ms)), signal);
        continue;
      }
      assertActive();
      if (value?.status === 'failed') {
        throw Object.assign(new Error('Read preparation failed'), {
          response: { status: 503, data: { detail: value.detail || { message: 'تعذر تجهيز البيانات. أعد المحاولة.' } } },
        });
      }
      return value;
    }
  } catch (error) {
    demand.release();
    throw error;
  } finally {
    signal?.removeEventListener('abort', release);
    owner.finish(demand);
    if (!readDemand) owner.dispose();
  }
}
