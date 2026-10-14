// Fixed, passive pilot only. No caller/model URL or executable code is accepted.
export const TARGET = 'https://amasi-sa.com/ar/طقم-سلسال-بتعليقات-مستطيلة-لامعة/p775397267';
// Pure DOM extraction. Never executes page scripts or accepts their instructions.
export function extractProductFacts() {
  const text = (value, limit) => String(value || '').replace(/\s+/g,' ').trim().slice(0,limit);
  const form = document.querySelector('#product-form');
  const selects = [...(form?.querySelectorAll('select') || [])];
  const element = form?.querySelector('salla-product-options[options]');
  const attribute = element?.getAttribute('options');
  let definitionStatus = 'unavailable';
  let definitions = [];
  if (attribute) {
    definitionStatus = 'invalid';
    if (attribute.length <= 65536) {
      try {
        const raw = JSON.parse(attribute);
        if (Array.isArray(raw) && raw.every(o => o && typeof o === 'object' && !Array.isArray(o))) {
          definitionStatus = raw.length > 20 ? 'truncated' : 'observed';
          const id = value => /^(?:[A-Za-z0-9_-]){1,120}$/.test(String(value ?? '')) ? String(value) : null;
          const amount = value => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1000000000 ? value : null;
          definitions = raw.slice(0,20).map(option => {
            const details = Array.isArray(option.details) ? option.details : [];
            if (details.length > 20) definitionStatus = 'truncated';
            return {
              id:id(option.id), label:text(option.name,120), type:text(option.type,80),
              required:option.required === true,
              conditional:option.visibility_condition != null || Boolean(option.condition_attributes),
              values:details.slice(0,20).filter(v => v && typeof v === 'object' && !Array.isArray(v)).map(v => ({
                id:id(v.id), label:text(v.name,120), additional_price:amount(v.additional_price),
                unavailable:typeof v.is_out === 'boolean' ? v.is_out : null,
              })),
            };
          });
        }
      } catch { /* Malformed/unavailable data must not imply no options. */ }
    }
  }
  return {
    title:text(document.title,300),
    heading:text(document.querySelector('h1')?.textContent,300),
    description:text(document.querySelector('meta[name="description"]')?.getAttribute('content'),1200),
    options:selects.slice(0,10).map(s => ({
      label:text(s.getAttribute('aria-label') || s.getAttribute('name'),120),
      values:[...s.options].slice(0,20).map(o => text(o.textContent,120)),
    })),
    options_truncated:selects.length > 10 || selects.some(s => s.options.length > 20),
    option_definition_status:definitionStatus,
    option_definitions:definitions,
    displayed_price_text:text(form?.querySelector('h2')?.textContent,120),
  };
}
export default async ({ page: initialPage, context }) => {
  // Production callers may supply only an AMASI product URL, never code or a
  // general browser destination. Validate independently of the Python caller.
  const target = context.target ?? TARGET;
  let targetUrl;
  try {
    targetUrl = new URL(target);
    const path = decodeURIComponent(targetUrl.pathname);
    if (targetUrl.origin !== 'https://amasi-sa.com' || targetUrl.username || targetUrl.password
        || targetUrl.search || targetUrl.hash || targetUrl.href !== target
        || /[%\\\x00-\x20\x7f]/.test(path) || path.split('/').some(p => p === '.' || p === '..')
        || !/^\/(?:ar\/)?(?:[^/]+\/)?p[0-9]{1,20}$/.test(path)) throw new Error('scope');
  } catch {
    // The historic fixed pilot constant contains Unicode; normalize only that
    // exact constant, never malformed caller input.
    if (target !== TARGET) return {protocol:'mezan-passive-product-v1',nonce:context.nonce,
      status:'blocked',bytes_read:0,blocked_requests:0,facts:null};
    targetUrl = new URL(TARGET);
  }
  const isolated = await initialPage.browser().createBrowserContext();
  let accepted = false;
  let outcome = 'blocked';
  let blockedRequests = 0;
  let bytesRead = 0;
  let htmlAccepted = false;
  const pending = new Set();
  try {
    const page = await isolated.newPage();
    await page.setJavaScriptEnabled(false);
    await page.setBypassServiceWorker(true);
    await page.setCacheEnabled(false);
    const cdp = await page.createCDPSession();
    const { frameTree } = await cdp.send('Page.getFrameTree');
    const fail = requestId => cdp.send('Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' });
    const handle = async event => {
      const requestId = event.requestId;
      if (event.responseStatusCode === undefined) {
        if (!accepted && event.frameId === frameTree.frame.id && event.resourceType === 'Document'
            && event.request.method === 'GET' && event.request.url === targetUrl.href) {
          accepted = true;
          await cdp.send('Fetch.continueRequest', { requestId });
        } else { blockedRequests++; await fail(requestId); }
        return;
      }
      const headers = Object.fromEntries((event.responseHeaders || []).map(h => [h.name.toLowerCase(), h.value]));
      if (event.responseStatusCode !== 200 || (headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'text/html'
          || Number(headers['content-length'] || 0) > 1000000) {
        outcome = 'unavailable'; await fail(requestId); return;
      }
      const { stream } = await cdp.send('Fetch.takeResponseBodyAsStream', { requestId });
      let binary = '';
      try {
        while (true) {
          const chunk = await cdp.send('IO.read', { handle: stream, size: 16384 });
          const part = chunk.base64Encoded ? atob(chunk.data) : new TextEncoder().encode(chunk.data).reduce((s,b) => s + String.fromCharCode(b), '');
          bytesRead += part.length;
          if (bytesRead > 1000000) { outcome = 'too_large'; await fail(requestId); return; }
          binary += part;
          if (chunk.eof) break;
        }
      } finally { await cdp.send('IO.close', { handle: stream }); }
      // Render the captured HTML under a deny-all policy. No original response
      // cookies, refresh headers or origin policy can enable page execution.
      await cdp.send('Fetch.fulfillRequest', { requestId, responseCode: 200,
        responseHeaders: [
          {name:'Content-Type',value:'text/html; charset=utf-8'},
          {name:'Content-Security-Policy',value:"default-src 'none'; script-src 'none'; connect-src 'none'; img-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; sandbox"},
          {name:'Cache-Control',value:'no-store'}], body:btoa(binary) });
      htmlAccepted = true;
    };
    cdp.on('Fetch.requestPaused', event => {
      const task = handle(event).catch(async () => { outcome = 'worker_error'; try { await fail(event.requestId); } catch {} });
      pending.add(task); task.finally(() => pending.delete(task));
    });
    await cdp.send('Fetch.enable', { patterns: [
      {urlPattern:'*',requestStage:'Request'}, {urlPattern:'*',requestStage:'Response'}] });
    try {
      await page.goto(targetUrl.href, {waitUntil:'domcontentloaded',timeout:20000});
      await Promise.all([...pending]);
      if (htmlAccepted && page.url() === targetUrl.href) {
        const facts = await page.evaluate(extractProductFacts);
        return {protocol:'mezan-passive-product-v1',nonce:context.nonce,status:'observed',
          bytes_read:bytesRead,blocked_requests:blockedRequests,facts};
      }
    } catch { /* Return bounded classifications only; never provider exceptions. */ }
    return {protocol:'mezan-passive-product-v1',nonce:context.nonce,status:outcome,
      bytes_read:bytesRead,blocked_requests:blockedRequests,facts:null};
  } finally { await isolated.close(); }
};
