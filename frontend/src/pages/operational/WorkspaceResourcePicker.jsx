import React, { useEffect, useRef, useState } from 'react';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { resourceKey } from './simpleBalancesModel';
import { assertWorkspace, retryWorkspaceRead, useWorkspaceRead, validResourcePage } from './workspaceReadHooks';

// This selector owns only its current search page. The selected resource is
// retained by the form; changing pages never turns it into a different party.
export default function WorkspaceResourcePicker({ context, operation = 'movement', role = 'recipient',
  kind = '', selectedResource, value, onSelect, label, disabled = false, optional = false,
  optionDisabled = () => false, enabled = true }) {
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [page, setPage] = useState(1);
  useEffect(() => {
    if (draft.trim() === query) return undefined;
    const timer = setTimeout(() => { setQuery(draft.trim()); setPage(1); }, 350);
    return () => clearTimeout(timer);
  }, [draft, query]);
  const key = JSON.stringify([context.actor.id, context.session_scope, context.permission_version,
    operation, role, kind, selectedResource?.kind, selectedResource?.id, query, page]);
  const completed = useRef(null);
  const read = useWorkspaceRead(async config => {
    const result = await api.options({ operation, role, ...(kind && { kind }), ...(query && { q: query }),
      page, page_size: 24, ...(selectedResource && { selected_kind: selectedResource.kind, selected_id: selectedResource.id }),
    }, config);
    assertWorkspace(result, context);
    if (!validResourcePage(result, context)) throw new Error('Invalid resource options');
    return result;
  }, key, enabled && (!disabled || completed.current === key));
  // Locking a completed selector during save must not discard its page and
  // issue the same read again when the mutation finishes. A new query never
  // inherits this allowance from the previous one.
  useEffect(() => { if (read.data) completed.current = key; }, [read.data, key]);
  const current = value && resourceKey(value);
  const items = read.data?.items || [];
  const choices = value && !items.some(item => resourceKey(item) === current) ? [value, ...items] : items;
  return <div className="field workspace-resource-picker">
    <label className="field"><span>{label}</span><input type="search" maxLength={160} value={draft} disabled={disabled}
      aria-label={`بحث · ${label}`} placeholder="ابحث بالاسم" onChange={event => setDraft(event.target.value)} /></label>
    {read.error ? <p className="note warning" role="alert">{read.error} <button type="button" onClick={()=>retryWorkspaceRead(read,context)}>إعادة المحاولة</button></p>
      : !read.data && enabled && !disabled ? <p role="status">جارٍ تحميل الخيارات…</p> : null}
    <select aria-label={label} value={current || ''} disabled={disabled || !read.data} required={!optional}
      onChange={event => onSelect(choices.find(item => resourceKey(item) === event.target.value) || null)}>
      <option value="">{optional ? 'الكل' : 'اختر من القائمة'}</option>
      {choices.map(item => <option key={resourceKey(item)} value={resourceKey(item)} disabled={optionDisabled(item)}>
        {item.name}{optionDisabled(item) ? ' — غير متاح للصرف' : ''}
      </option>)}
    </select>
    {read.data && !items.length && <small role="status">لا توجد خيارات متاحة تطابق البحث.</small>}
    {read.data?.pages > 1 && <nav className="movement-pages" aria-label={`صفحات · ${label}`}>
      <button type="button" className="textbtn" disabled={read.data.page <= 1} onClick={() => setPage(read.data.page - 1)}>السابق</button>
      <span>{read.data.page} من {read.data.pages}</span>
      <button type="button" className="textbtn" disabled={read.data.page >= read.data.pages} onClick={() => setPage(read.data.page + 1)}>التالي</button>
    </nav>}
  </div>;
}
