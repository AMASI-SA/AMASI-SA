/* Private PR2095 Preview observer. No application requests or credentials. */
export const VERSION = 'pr2095-passive-observer-v1';
export const VIEWS = Object.freeze({
  home: 'الرئيسية', assets: 'الأصول والحسابات', log: 'سجل الحركات',
  parties: 'الأشخاص والجهات', reviews: 'تحتاج مراجعة', position: 'المركز المالي',
  reports: 'التقارير', tools: 'العمليات والكشوف',
});
export const REPORTS = Object.freeze({
  summary: 'الصورة الكاملة', profit: 'المبيعات والربح', providers: 'منصات الدفع والمرتجعات',
  accounts: 'البنوك والصناديق', suppliers: 'الموردون', employees: 'الرواتب والعهد',
  couriers: 'الشحن والتحصيل', drivers: 'الموصلون', advertising: 'الإعلانات',
  other: 'الجهات والمحافظ', expected: 'المبالغ والتكاليف المحتملة', reconciliation: 'المطابقة والمصادر',
});
const BASE = '/operational-balances';
const SPECIAL = Object.freeze({
  'provider-fees': ['تعرفات وعمولات المنصات', 'tools'],
  'provider-statements': ['مطابقة كشوف منصات الدفع', 'tools'],
  'financial-position': ['المركز المالي', 'position'],
  'customer-returns': ['مرتجعات العملاء', 'tools'],
  'customer-exchanges': ['استبدال الأصناف', 'tools'],
  access: ['صلاحيات الأرصدة', 'tools'],
  'shipping-review': ['مراجعة أجور الشحن', 'reviews'],
  'provider-history': ['مراجعة الطلبات السابقة', 'reviews'],
});
const PANEL = 'mezan-pr2095-observer';
const LIMITS = Object.freeze({ samples: 300, resources: 2000, longTasks: 1000 });
const rounded = n => Number.isFinite(n) ? Math.round(n * 1000) / 1000 : null;
const normalized = text => String(text || '').replace(/\s+/g, ' ').trim();

// Return only fixed route vocabulary. Never preserve IDs, arbitrary parameters,
// hashes, origin, user names, dates, search terms, or the original URL.
export function safeRoute(raw, origin) {
  let url;
  try { url = new URL(raw, origin); } catch { return null; }
  if (url.origin !== origin) return null;
  if (url.pathname === BASE || url.pathname === BASE + '/daily-movements') {
    if (url.pathname.endsWith('/daily-movements') && !url.searchParams.has('view')
      && ['tab', 'panel', 'dialog', 'action', 'focus_id', 'kind', 'id'].some(k => url.searchParams.has(k))) return null;
    const view = url.searchParams.get('view') || (url.pathname.endsWith('/daily-movements') ? 'log' : 'home');
    if (!Object.hasOwn(VIEWS, view)) return null;
    const tab = view === 'reports' ? url.searchParams.get('report_tab') || 'summary' : null;
    if (tab && !Object.hasOwn(REPORTS, tab)) return null;
    return { path: url.pathname, view, report_tab: tab,
      key: `${url.pathname}?view=${view}${tab ? '&report_tab=' + tab : ''}` };
  }
  const leaf = url.pathname.startsWith(BASE + '/') ? url.pathname.slice(BASE.length + 1) : '';
  if (!Object.hasOwn(SPECIAL, leaf)) return null;
  const requestedView = url.searchParams.get('view');
  const view = Object.hasOwn(REPORTS, requestedView) ? requestedView : null;
  return { path: url.pathname, view, report_tab: null,
    key: url.pathname + (view ? '?view=' + view : '') };
}

export function resourceKind(raw, origin) {
  let url;
  try { url = new URL(raw, origin); } catch { return null; }
  if (url.origin !== origin) return null;
  // The private gateway may be mounted at /pr2095/api instead of /api.
  const path = url.pathname.replace(/^\/pr2095(?=\/api\/)/, '');
  if (path === '/api/auth/me') return 'auth-me';
  if (!path.startsWith('/api/operational-balances/')) return null;
  const endpoint = path.slice('/api/operational-balances/'.length);
  const known = new Set(['workspace/bootstrap', 'workspace/home', 'workspace/directory',
    'workspace/activity', 'workspace/statement', 'workspace/selection', 'workspace/report',
    'workspace/position', 'workspace/reviews', 'financial-position', 'automation', 'context']);
  if (known.has(endpoint)) return endpoint;
  if (endpoint.startsWith('workspace/read-demand/')) return 'workspace/read-demand/:redacted';
  return 'operational-other';
}

export function summarize(values) {
  const sorted = values.filter(n => Number.isFinite(n) && n >= 0).sort((a, b) => a - b);
  const n = sorted.length;
  return { n, p50_ms: n ? rounded(n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2) : null,
    p95_ms: n >= 20 ? rounded(sorted[Math.ceil(n * 0.95) - 1]) : null,
    p95_unavailable_reason: n < 20 ? 'fewer_than_20_valid_samples' : null,
    max_ms: n ? rounded(sorted[n - 1]) : null };
}

function startObserver(script) {
  if (document.getElementById(PANEL)) return;
  const expectedHost = script.dataset.allowedHost;
  if (!expectedHost || location.hostname !== expectedHost || expectedHost === 'mezansalla.com') return;
  const sourceSha = /^[0-9a-f]{40}$/.test(script.dataset.sourceSha || '') ? script.dataset.sourceSha : null;
  const overlaySha = /^[0-9a-f]{64}$/.test(script.dataset.overlaySha || '') ? script.dataset.overlaySha : null;
  if (!sourceSha || !overlaySha || !['baseline', 'candidate'].includes(script.dataset.arm)) return;
  const started = performance.now();
  const state = { recording: false, active: null, samples: [], resources: [], longTasks: [],
    dropped: { samples: 0, resources: 0, longTasks: 0 }, nextId: 1, seen: new Set(),
    scanCount: 0, scanMs: 0, supported: {}, frame: 0, pendingRender: false };
  const main = () => document.getElementById('finance-main');
  const route = () => safeRoute(location.href, location.origin);
  const rendered = el => Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
  const outsideNotices = el => !el.closest('.balance-freshness, #' + PANEL);
  const visibleMatches = selector => [...(main()?.querySelectorAll(selector) || [])].filter(el => outsideNotices(el) && rendered(el));
  const loading = () => visibleMatches('[role="status"]').some(el => /^جار[ٍي]?\s/.test(normalized(el.textContent)));
  const pendingReport = () => visibleMatches('.approved-reports [role="status"]')
    .some(el => normalized(el.textContent).startsWith('جارٍ قراءة التقرير'));
  const addBounded = (key, value) => {
    if (state[key].length === LIMITS[key]) { state[key].shift(); state.dropped[key]++; }
    state[key].push(value);
  };
  const elapsed = (sample, now = performance.now()) => rounded(Math.max(0, now - sample.click_t));

  function shellMatches(target) {
    const area = main();
    if (!rendered(area)) return false;
    const selected = document.querySelector('.mz-finance .nav a[aria-current="page"]');
    const selectedRoute = selected && safeRoute(selected.href, location.origin);
    const active = selectedRoute && (SPECIAL[selectedRoute.path.slice(BASE.length + 1)]?.[1] || selectedRoute.view);
    const breadcrumb = normalized(document.querySelector('.mz-finance .bar .trail b')?.textContent);
    const title = normalized(area.querySelector('.heading h1')?.textContent);
    if (target.path === BASE || target.path.endsWith('/daily-movements')) {
      if (active !== target.view || breadcrumb !== VIEWS[target.view]) return false;
      if (title !== (target.view === 'home' ? 'الأرصدة التشغيلية' : VIEWS[target.view])) return false;
      if (target.view === 'reports') {
        const tab = area.querySelector('.report-tabs button[aria-current="page"]');
        if (!rendered(tab) || normalized(tab.textContent) !== REPORTS[target.report_tab]) return false;
      }
      return true;
    }
    const spec = SPECIAL[target.path.slice(BASE.length + 1)];
    // Specialized pages retain the same shell, but their data readiness stays unknown.
    return Boolean(spec && active === spec[1] && breadcrumb === spec[0] && rendered(area.querySelector('.mz-specialized')));
  }

  function dataState(target) {
    if (visibleMatches('[role="alert"]').length) return 'error_visible';
    if (loading()) return 'loading_visible';
    if (target.view === 'reports' && (target.path === BASE || target.path.endsWith('/daily-movements'))) {
      if (visibleMatches('.approved-reports .report-section-title').length) return 'ready_proxy';
      if (visibleMatches('.approved-reports .report-scope').length && visibleMatches('.approved-reports .empty').length) return 'empty_proxy';
      return 'unknown';
    }
    if (target.path !== BASE && !target.path.endsWith('/daily-movements')) return 'unknown';
    if (['reviews', 'tools'].includes(target.view)) return 'navigation_only';
    if (visibleMatches('[aria-label="تاريخ البيانات المحفوظة"]').length) return 'ready_proxy';
    if (target.view === 'home' && visibleMatches('.main-grid .hero').length) return 'ready_proxy';
    if (visibleMatches('.empty').length) return 'empty_proxy';
    // No loading text alone is not sufficient evidence that data was received.
    return 'unknown';
  }

  function finish(reason) {
    const sample = state.active;
    if (!sample) return;
    clearTimeout(sample.timeout);
    sample.end_t = rounded(performance.now());
    sample.finish_reason = reason;
    if (sample.data_state === 'loading_visible') sample.data_state = reason === 'next_navigation' ? 'left_before_ready' : sample.data_state;
    state.active = null;
    scheduleRender();
  }

  function scan() {
    const sample = state.active;
    if (!sample) return;
    const t = performance.now();
    state.scanCount++;
    try {
      if (document.visibilityState !== 'visible') { sample.invalid_reason = 'document_hidden'; finish('hidden'); return; }
      if (route()?.key !== sample.target.key) return;
      if (sample.url_observed_ms === null) sample.url_observed_ms = elapsed(sample, t);
      if (!shellMatches(sample.target)) return;
      if (sample.shell_committed_ms === null) sample.shell_committed_ms = elapsed(sample, t);
      if (sample.shell_two_raf_task_proxy_ms === null && !sample.probe_pending) {
        sample.probe_pending = true;
        requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(() => {
          sample.probe_pending = false;
          if (state.active !== sample || document.visibilityState !== 'visible' || route()?.key !== sample.target.key || !shellMatches(sample.target)) return;
          sample.shell_two_raf_task_proxy_ms = elapsed(sample);
          scan();
          scheduleRender();
        }, 0)));
      }
      const next = dataState(sample.target);
      sample.data_state = next;
      if (['ready_proxy', 'empty_proxy'].includes(next) && sample.data_ready_proxy_ms === null) sample.data_ready_proxy_ms = elapsed(sample);
      if (sample.shell_two_raf_task_proxy_ms !== null && ['ready_proxy', 'empty_proxy', 'navigation_only', 'error_visible'].includes(next)) finish(next);
    } finally { state.scanMs += performance.now() - t; }
  }

  function frame() {
    state.frame = 0;
    if (!state.active) return;
    scan();
    const s = state.active;
    // Stop per-frame work once shell settles. Data changes remain observed passively.
    if (s && s.shell_two_raf_task_proxy_ms === null && performance.now() - s.click_t < 15000) state.frame = requestAnimationFrame(frame);
  }

  function click(event) {
    if (!state.recording || !event.isTrusted || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const element = event.target instanceof Element ? event.target : event.target?.parentElement;
    if (!element || element.closest('#' + PANEL)) return;
    const from = route();
    let target;
    const reportButton = element.closest('.approved-reports .report-tabs button');
    if (reportButton && !reportButton.disabled) {
      const key = Object.keys(REPORTS).find(k => REPORTS[k] === normalized(reportButton.textContent));
      if (key && from?.view === 'reports') target = safeRoute(from.path + '?view=reports&report_tab=' + key, location.origin);
    } else {
      const link = element.closest('.mz-finance a[href]');
      if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
      target = safeRoute(link.href, location.origin);
    }
    if (!target || !from || from.key === target.key) return;
    const now = performance.now();
    let stamp = event.timeStamp;
    if (stamp > performance.timeOrigin) stamp -= performance.timeOrigin;
    const validStamp = Number.isFinite(stamp) && stamp >= 0 && stamp <= now + 1 && now - stamp < 120000;
    const sourcePending = pendingReport();
    finish('next_navigation');
    const sample = { id: state.nextId++, from: from.key, target, cache_label: cacheSelect.value,
      first_observed_visit_in_document: !state.seen.has(target.key), click_t: rounded(validStamp ? stamp : now),
      timestamp_source: validStamp ? 'event.timeStamp' : 'handler_fallback',
      input_queue_delay_ms: validStamp ? rounded(Math.max(0, now - stamp)) : null,
      source_report_pending_at_click: sourcePending, url_observed_ms: null, shell_committed_ms: null,
      shell_two_raf_task_proxy_ms: null, data_ready_proxy_ms: null, data_state: 'unknown',
      invalid_reason: validStamp ? null : 'event_timestamp_unavailable', finish_reason: null, end_t: null, timeout: null, probe_pending: false };
    state.seen.add(target.key);
    addBounded('samples', sample);
    state.active = sample;
    sample.timeout = setTimeout(() => { if (state.active === sample) finish('120s_observation_limit'); }, 120000);
    if (!state.frame) state.frame = requestAnimationFrame(frame);
  }

  function observe(type, callback) {
    state.supported[type] = Boolean(globalThis.PerformanceObserver?.supportedEntryTypes?.includes(type));
    if (!state.supported[type]) return;
    try {
      new PerformanceObserver(list => { if (state.recording) callback(list.getEntries()); })
        .observe({ type, buffered: true });
    } catch { state.supported[type] = false; }
  }
  observe('longtask', entries => entries.forEach(e => {
    if (e.startTime >= started) addBounded('longTasks', { start_t: rounded(e.startTime), duration_ms: rounded(e.duration) });
  }));
  observe('resource', entries => entries.forEach(e => {
    if (!['fetch', 'xmlhttprequest'].includes(e.initiatorType) || e.responseEnd < started) return;
    const kind = resourceKind(e.name, location.origin);
    if (!kind) return;
    addBounded('resources', { kind, start_t: rounded(e.startTime), end_t: rounded(e.responseEnd),
      duration_ms: rounded(e.duration), response_status: Number.isInteger(e.responseStatus) && e.responseStatus > 0 ? e.responseStatus : null,
      initiator: e.initiatorType, transfer_bytes: Number.isFinite(e.transferSize) ? e.transferSize : null });
  }));
  document.addEventListener('click', click, { capture: true, passive: true });
  document.addEventListener('visibilitychange', () => {
    if (state.active && document.visibilityState !== 'visible') { state.active.invalid_reason = 'document_hidden'; finish('hidden'); }
  }, { passive: true });
  window.addEventListener('pagehide', () => finish('document_unloaded_results_not_persisted'), { passive: true });
  new MutationObserver(records => {
    if (state.active && records.some(r => !((r.target instanceof Element ? r.target : r.target.parentElement)?.closest('#' + PANEL)))) scan();
  }).observe(document.documentElement, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['aria-current', 'class', 'hidden', 'aria-busy'] });
  window.addEventListener('popstate', scan, { passive: true });

  function result() {
    const samples = state.samples.map(({ timeout, probe_pending, target, ...s }) => {
      const navigationEnd = s.click_t + (s.shell_two_raf_task_proxy_ms ?? s.url_observed_ms ?? 0);
      const long = state.longTasks.filter(t => t.start_t < navigationEnd && t.start_t + t.duration_ms > s.click_t);
      const overlap = state.resources.filter(r => r.start_t < s.click_t && r.end_t > s.click_t);
      const longReason = !state.supported.longtask ? 'performance_entry_type_unsupported'
        : s.shell_two_raf_task_proxy_ms === null ? 'shell_interval_incomplete' : null;
      return { ...s, to: target.key, eligible_shell_proxy: !s.invalid_reason && s.shell_two_raf_task_proxy_ms !== null,
        navigation_long_task_count: longReason ? null : long.length,
        navigation_long_task_overlap_ms: longReason ? null : rounded(long.reduce((sum, t) => sum + Math.max(0, Math.min(t.start_t + t.duration_ms, navigationEnd) - Math.max(t.start_t, s.click_t)), 0)),
        navigation_long_task_unavailable_reason: longReason,
        retrospective_completed_resource_overlap_count: state.supported.resource ? overlap.length : null,
        resource_overlap_unavailable_reason: state.supported.resource ? null : 'performance_entry_type_unsupported' };
    });
    const grouped = new Map();
    for (const sample of samples) {
      const key = sample.cache_label + '|' + sample.to;
      if (!grouped.has(key)) grouped.set(key, { to: sample.to, cache_label: sample.cache_label, samples: [] });
      grouped.get(key).samples.push(sample);
    }
    return { schema: 1, observer: VERSION, source_sha: sourceSha, observer_sha256: overlaySha,
      arm: script.dataset.arm, generated_at_utc: new Date().toISOString(),
      viewport: { width: innerWidth, height: innerHeight, device_pixel_ratio: devicePixelRatio },
      capabilities: state.supported, limits: LIMITS, dropped: { ...state.dropped },
      overhead: { scan_count: state.scanCount, measured_scan_ms: rounded(state.scanMs) },
      interpretation: { url_timing: 'first passive observation; not exact pushState time',
        shell_timing: 'selected route and heading, two requestAnimationFrame callbacks, then one task; interactivity proxy only',
        data_timing: 'DOM readiness/empty-state proxy; not completeness or accounting correctness',
        cold_warm: 'operator label; cache state not inferred or changed',
        http_methods_available: false, request_bodies_or_headers_captured: false,
        pending_resources: 'completed ResourceTiming overlap is retrospective and incomplete; not an in-flight request inventory',
        bounded_buffers: 'overlap metrics cover retained observed entries only; dropped entries and browser delivery gaps make evidence incomplete',
        p95: 'nearest rank, only for at least 20 eligible samples per route/cache label',
        survives_document_reload: false, financial_write_safety_proven_by_observer: false },
      groups: [...grouped.values()].map(g => ({ to: g.to, cache_label: g.cache_label,
        recorded: g.samples.length, shell_proxy: summarize(g.samples.filter(s => s.eligible_shell_proxy).map(s => s.shell_two_raf_task_proxy_ms)),
        url_observed: summarize(g.samples.filter(s => !s.invalid_reason && s.url_observed_ms !== null).map(s => s.url_observed_ms)),
        data_ready_proxy: summarize(g.samples.filter(s => !s.invalid_reason && s.data_ready_proxy_ms !== null).map(s => s.data_ready_proxy_ms)),
        visible_error_samples: g.samples.filter(s => s.data_state === 'error_visible').length })),
      samples, long_tasks: [...state.longTasks], resource_timings: [...state.resources] };
  }

  const panel = document.createElement('details');
  panel.id = PANEL;
  panel.dir = 'rtl';
  const summary = document.createElement('summary');
  summary.textContent = 'قياس المعاينة · متوقف';
  panel.append(summary);
  const description = document.createElement('p');
  description.textContent = 'قياس مؤقت: تنقّل بالروابط الحقيقية. ظهور الصفحة مؤشر للاستجابة، واكتمال البيانات قياس منفصل.';
  panel.append(description);
  const controls = document.createElement('div');
  const cacheLabel = document.createElement('label');
  cacheLabel.textContent = 'تصنيف الفاحص ';
  const cacheSelect = document.createElement('select');
  cacheSelect.setAttribute('aria-label', 'تصنيف حرارة الذاكرة للاختبار');
  for (const [value, label] of [['unclassified', 'غير مصنف'], ['cold', 'بارد — يحدده الفاحص'], ['warm', 'دافئ — يحدده الفاحص']]) {
    const option = document.createElement('option'); option.value = value; option.textContent = label; cacheSelect.append(option);
  }
  cacheLabel.append(cacheSelect); controls.append(cacheLabel); panel.append(controls);
  const buttons = document.createElement('div'); panel.append(buttons);
  const button = (label, handler) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.addEventListener('click', handler); buttons.append(b); return b; };
  const toggle = button('بدء القياس', () => {
    state.recording = !state.recording;
    if (!state.recording) finish('operator_paused');
    toggle.textContent = state.recording ? 'إيقاف القياس' : 'بدء القياس';
    render();
  });
  const output = document.createElement('output'); output.setAttribute('aria-label', 'ملخص قياسات التنقل'); panel.append(output);
  const jsonArea = document.createElement('textarea'); jsonArea.readOnly = true; jsonArea.hidden = true;
  jsonArea.setAttribute('aria-label', 'نتائج القياس المنقحة JSON'); jsonArea.dir = 'ltr'; panel.append(jsonArea);
  button('عرض JSON', () => { jsonArea.hidden = false; jsonArea.value = JSON.stringify(result(), null, 2); });
  button('تنزيل JSON', () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(result(), null, 2) + '\n'], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = 'pr2095-preview-observations.json';
    a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  button('مسح القياس المحلي', () => {
    finish('operator_cleared');
    state.samples.length = 0; state.resources.length = 0; state.longTasks.length = 0;
    state.seen.clear(); state.nextId = 1; state.scanCount = 0; state.scanMs = 0;
    state.dropped = { samples: 0, resources: 0, longTasks: 0 };
    jsonArea.value = ''; jsonArea.hidden = true; render();
  });
  function render() {
    state.pendingRender = false;
    const snapshot = result();
    const valid = snapshot.samples.filter(s => s.eligible_shell_proxy);
    summary.textContent = `قياس المعاينة · ${state.recording ? 'يعمل' : 'متوقف'} · ${valid.length} عينة`;
    const latest = valid[valid.length - 1];
    output.textContent = latest ? `آخر انتقال: ${latest.to}\nمؤشر الاستجابة: ${latest.shell_two_raf_task_proxy_ms} ms\nحالة البيانات: ${latest.data_state}\nP95 لكل مسار يظهر في JSON بعد 20 عينة صالحة.` : 'لا توجد عينة استجابة صالحة بعد.';
  }
  function scheduleRender() {
    if (state.pendingRender) return;
    state.pendingRender = true;
    if ('requestIdleCallback' in window) window.requestIdleCallback(render, { timeout: 1000 });
    else setTimeout(render, 100);
  }
  document.body.append(panel);
  render();
}

if (typeof document !== 'undefined') {
  const script = document.querySelector('script[data-mezan-preview-observer="1"]');
  if (script) startObserver(script);
}
