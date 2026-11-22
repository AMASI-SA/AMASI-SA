import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { AdBalance, Amount, Heading, Icon, ResourceIcon, assetGroups, isAsset, issueLabel, partyGroups, searchable, styleOf } from './ApprovedFinanceUI';
import { minorUnits, signedResourceBalance } from './simpleBalancesModel';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { assertWorkspace, retryWorkspaceRead, useWorkspaceRead, validResourcePage } from './workspaceReadHooks';
import WorkspaceSnapshot from './WorkspaceSnapshot';
import { CustomerAdjustmentBadges } from './CustomerAdjustments';

export function ProviderAmount({ resource }) {
  const position=resource.provider_position;
  if (!resource.access?.balance || resource.balance?.visible===false) return <small>عرض الحساب</small>;
  if (!position) {
    const balance=resource.balance;
    return minorUnits(balance?.amount)===null ? <small>يحتاج مطابقة</small> :
      <><Amount value={balance.side==='for_party'?'-'+balance.amount:balance.amount} currency={resource.currency}/><small className="under">الرصيد المسجّل</small></>;
  }
  const value=position.complete ? position.net : position.known_net;
  const available=minorUnits(value)!==null;
  return <>{available ? <Amount value={value} currency={position.currency}/> : <small>يحتاج مطابقة</small>}
    {available && !position.complete && <small className="under">الصافي المحتسب · يحتاج مطابقة</small>}
    {(minorUnits(position.uncomputed_principal)>0n || position.uncomputed_principal_complete===false) &&
      <small className="under">قيمة الطلبات غير المحتسبة قبل العمولة: <Amount value={position.uncomputed_principal} currency={position.currency}/>
        {position.uncomputed_principal_complete===false && ' · القيمة المعروفة فقط'}
        {resource.access.movements==='all' && resource.access.report && Number.isInteger(position.uncomputed_orders) && ` · ${position.uncomputed_orders} طلب`}
        <span className="under">غير مشمولة في الصافي المحتسب · تحتاج مطابقة</span></small>}
    {resource.access.movements==='all' && resource.access.report && Number.isInteger(position.paid_orders) &&
      <small className="under">الطلبات المدفوعة · {position.paid_orders}</small>}</>;
}

export function ResourceCard({ resource, href }) {
  const [label] = styleOf(resource), value=resource.balance;
  const visible=resource.access?.balance && value?.visible !== false && minorUnits(value?.amount) !== null;
  const actual=['bank','cash'].includes(resource.kind) ? signedResourceBalance(resource) : (isAsset(resource.kind)&&value?.side==='for_party' ? '-'+value.amount : value?.amount);
  const Card = href ? Link : 'div';
  return <Card className="resource" {...(href ? {to:href} : {})}>
    <div className="between"><ResourceIcon resource={resource} /><span className="under">{label}</span></div>
    <strong>{resource.name}</strong><div className="number">{resource.kind==='provider' ? <ProviderAmount resource={resource}/> :
      visible ? (resource.kind === 'ad_account' ? <AdBalance resource={resource} /> : <Amount value={actual} currency={resource.currency} />) : <small>عرض الحساب</small>}</div>
    {visible && !isAsset(resource.kind) && <span className="under">{value.side==='for_party'?'له عند المؤسسة':value.side==='for_us'?'عليه للمؤسسة':'الحساب متوازن'}</span>}
    {resource.order_issues_count > 0 && <span className="issue-badge"><Icon name="circle-alert"/>{issueLabel(resource.order_issues_count)}</span>}
    {resource.kind === 'provider' && resource.access?.movements === 'all' && resource.access?.report && <CustomerAdjustmentBadges summary={resource.customer_adjustments || resource.provider_position?.customer_adjustments}/>}
  </Card>;
}

export default function ApprovedDirectory(props) {
  return props.context.demandOnly ? <DemandDirectory {...props}/> : <LegacyDirectory {...props}/>;
}

function DemandDirectory({ context, people, to, onAdd, onCreateCash }) {
  const [filter, setFilter] = useState(''), [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [page, setPage] = useState(1);
  const groups = people ? partyGroups : assetGroups;
  const kind = groups.find(([name]) => name === filter)?.[1].join(',') || '';
  useEffect(() => {
    if (draft.trim() === query) return undefined;
    const timer = setTimeout(() => { setQuery(draft.trim()); setPage(1); }, 350);
    return () => clearTimeout(timer);
  }, [draft, query]);
  const key = JSON.stringify([context.actor.id, context.session_scope, context.permission_version, people, kind, query, page]);
  const read = useWorkspaceRead(async config => {
    const result = await api.directory({ group: people ? 'parties' : 'assets', ...(kind && { kind }),
      ...(query && { q: query }), page, page_size: 24 }, config);
    assertWorkspace(result, context);
    if (!validResourcePage(result, context)) throw new Error('Invalid directory page');
    return result;
  }, key);
  const result = read.data;
  return <><Heading title={people ? 'الأشخاص والجهات' : 'الأصول والحسابات'} subtitle={people ? 'ابحث بالاسم وافتح الكشف لمعرفة كم له أو عليه.' : 'اختر الحساب لعرض رصيده وحركاته.'}>
    {onAdd && <button className="button primary" onClick={onAdd}><Icon name="plus"/>حركة مالية جديدة</button>}
    {onCreateCash && <button type="button" className="button" onClick={onCreateCash}><Icon name="plus"/>إضافة صندوق نقدي</button>}
  </Heading>
    <label className="searchbox directory-search"><Icon name="search"/><input type="search" maxLength={160} aria-label={people ? 'بحث باسم الشخص أو الجهة' : 'بحث باسم الحساب'} value={draft}
      onChange={event => setDraft(event.target.value)} placeholder={people ? 'ابحث باسم الشخص أو الجهة' : 'ابحث باسم الحساب'}/></label>
    <div className="tabset">{['', ...groups.map(([name]) => name)].map(name => <button key={name} type="button" aria-pressed={filter === name}
      className={filter === name ? 'on' : ''} onClick={() => { setFilter(name); setPage(1); }}>{name || 'الكل'}</button>)}</div>
    {read.error ? <p role="alert" className="note warning">{read.error} <button type="button" onClick={()=>retryWorkspaceRead(read,context)}>إعادة المحاولة</button></p>
      : !result ? <p role="status">جارٍ تحميل الحسابات…</p> : <>
        <WorkspaceSnapshot snapshot={result.snapshot}/>
        {groups.map(([name, kinds]) => {
          const items = result.items.filter(item => kinds.includes(item.kind));
          return items.length > 0 && <section key={name}><div className="section-head"><h2 className="card-grid-title">{name}</h2></div>
            <div className="cards">{items.map(item => <ResourceCard key={item.kind + ':' + item.id} resource={item} href={to('ledger', item)}/>)}</div></section>;
        })}
        {!result.total && <p className="empty">{onCreateCash && !query && (!kind || kind === 'cash') ? 'لا توجد صناديق مسجّلة.' : 'لا توجد نتائج ضمن الحسابات المتاحة لك.'}</p>}
        {result.pages > 1 && <nav className="movement-pages" aria-label="صفحات الحسابات">
          <span>{(result.page - 1) * 24 + 1}–{Math.min(result.page * 24, result.total)} من {result.total}</span><div>
            <button type="button" className="button" disabled={result.page <= 1} onClick={() => setPage(result.page - 1)}>السابق</button>
            <button type="button" className="button" disabled={result.page >= result.pages} onClick={() => setPage(result.page + 1)}>التالي</button>
          </div></nav>}
      </>}
  </>;
}

function LegacyDirectory({ context, people, to, onAdd, onCreateCash }) {
  const [filter,setFilter]=useState(''), [query,setQuery]=useState(''), [page,setPage]=useState(1);
  const groups=people?partyGroups:assetGroups;
  const matches=useMemo(()=>{
    const term=searchable(query);
    return groups.flatMap(([name,kinds]) => filter && name!==filter ? [] :
      context.resources.filter(r=>kinds.includes(r.kind) && searchable(r.name).includes(term)).map(r=>({name,r})));
  },[context.resources, groups, filter, query]);
  const size=24, current=Math.min(page,Math.max(1,Math.ceil(matches.length/size)));
  const displayed=matches.slice((current-1)*size,current*size);
  const cashGroupName=assetGroups.find(([,kinds])=>kinds.includes('cash'))[0];
  const showEmptyCash=Boolean(!people && onCreateCash && !query && (!filter || filter===cashGroupName)
    && current===1 && !context.resources.some(r=>r.kind==='cash'));
  return <><Heading title={people?'الأشخاص والجهات':'الأصول والحسابات'} subtitle={people?'ابحث بالاسم وافتح الكشف لمعرفة كم له أو عليه.':'اختر الحساب لعرض رصيده وحركاته.'}>
    {onAdd && <button className="button primary" onClick={onAdd}><Icon name="plus"/>حركة مالية جديدة</button>}</Heading>
    <label className="searchbox directory-search"><Icon name="search"/><input type="search" aria-label={people?'بحث باسم الشخص أو الجهة':'بحث باسم الحساب'} value={query}
      onChange={e=>{setQuery(e.target.value);setPage(1)}} placeholder={people?'ابحث باسم الشخص أو الجهة':'ابحث باسم الحساب'}/></label>
    <div className="tabset">{['',...groups.filter(([,kinds])=>kinds.includes('cash')&&onCreateCash || context.resources.some(r=>kinds.includes(r.kind))).map(([name])=>name)].map(name=>
      <button key={name} type="button" aria-pressed={filter===name} className={filter===name?'on':''} onClick={()=>{setFilter(name);setPage(1)}}>{name||'الكل'}</button>)}</div>
    {groups.map(([name,kinds])=>{
      const group=displayed.filter(x=>x.name===name);
      const cashGroup=Boolean(onCreateCash && kinds.includes('cash'));
      const emptyCash=cashGroup && showEmptyCash;
      return (group.length>0 || emptyCash) && <section key={name}>
      <div className="section-head"><h2 className="card-grid-title">{name}</h2>
        {cashGroup && <button type="button" className="button" onClick={onCreateCash}><Icon name="plus"/>إضافة صندوق نقدي</button>}</div>
      {emptyCash && <p className="under">لا توجد صناديق مسجّلة.</p>}
      <div className="cards">{group.map(({r})=><ResourceCard key={r.kind+':'+r.id} resource={r} href={to('ledger',r)}/>)}</div></section>})}
    {matches.length===0 && !showEmptyCash && <p className="empty">لا توجد نتائج ضمن الحسابات المتاحة لك.</p>}
    {matches.length>size && <nav className="movement-pages" aria-label="صفحات الحسابات"><span>{(current-1)*size+1}–{Math.min(current*size,matches.length)} من {matches.length}</span><div>
      <button className="button" disabled={current===1} onClick={()=>setPage(current-1)}>السابق</button><button className="button" disabled={current*size>=matches.length} onClick={()=>setPage(current+1)}>التالي</button></div></nav>}
  </>;
}
