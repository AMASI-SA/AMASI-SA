import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Amount, Heading, Icon, IconBox, assetGroups, isAsset, issueLabel, partyGroups, searchable, styleOf } from './ApprovedFinanceUI';
import { minorUnits, signedResourceBalance } from './simpleBalancesModel';

export function ResourceCard({ resource, href }) {
  const [label, icon, tone] = styleOf(resource), value=resource.balance;
  const visible=resource.access?.balance && value?.visible !== false && minorUnits(value?.amount) !== null;
  const actual=resource.kind==='ad_account' ? resource.ad_wallet_balance : ['bank','cash'].includes(resource.kind) ? signedResourceBalance(resource) : (isAsset(resource.kind)&&value?.side==='for_party' ? '-'+value.amount : value?.amount);
  return <Link className="resource" to={href}>
    <div className="between"><IconBox name={icon} tone={tone} /><span className="under">{label}</span></div>
    <strong>{resource.name}</strong><div className="number">{visible ? <Amount value={actual} currency={resource.currency} /> : <small>عرض الحساب</small>}</div>
    {visible && !isAsset(resource.kind) && <span className="under">{value.side==='for_party'?'له عند المؤسسة':value.side==='for_us'?'عليه للمؤسسة':'الحساب متوازن'}</span>}
    {resource.order_issues_count > 0 && <span className="issue-badge"><Icon name="circle-alert"/>{issueLabel(resource.order_issues_count)}</span>}
  </Link>;
}

export default function ApprovedDirectory({ context, people, to, onAdd }) {
  const [filter,setFilter]=useState(''), [query,setQuery]=useState(''), [page,setPage]=useState(1);
  const groups=people?partyGroups:assetGroups;
  const matches=useMemo(()=>{
    const term=searchable(query);
    return groups.flatMap(([name,kinds]) => filter && name!==filter ? [] :
      context.resources.filter(r=>kinds.includes(r.kind) && searchable(r.name).includes(term)).map(r=>({name,r})));
  },[context.resources, groups, filter, query]);
  const size=24, current=Math.min(page,Math.max(1,Math.ceil(matches.length/size)));
  const displayed=matches.slice((current-1)*size,current*size);
  return <><Heading title={people?'الأشخاص والجهات':'الأصول والحسابات'} subtitle={people?'ابحث بالاسم وافتح الكشف لمعرفة كم له أو عليه.':'اختر الحساب لعرض رصيده وحركاته.'}>
    {onAdd && <button className="button primary" onClick={onAdd}><Icon name="plus"/>حركة مالية جديدة</button>}</Heading>
    <label className="searchbox directory-search"><Icon name="search"/><input type="search" aria-label={people?'بحث باسم الشخص أو الجهة':'بحث باسم الحساب'} value={query}
      onChange={e=>{setQuery(e.target.value);setPage(1)}} placeholder={people?'ابحث باسم الشخص أو الجهة':'ابحث باسم الحساب'}/></label>
    <div className="tabset">{['',...groups.filter(([,kinds])=>context.resources.some(r=>kinds.includes(r.kind))).map(([name])=>name)].map(name=>
      <button key={name} type="button" aria-pressed={filter===name} className={filter===name?'on':''} onClick={()=>{setFilter(name);setPage(1)}}>{name||'الكل'}</button>)}</div>
    {groups.map(([name])=>{const group=displayed.filter(x=>x.name===name);return group.length>0 && <section key={name}><h2 className="card-grid-title">{name}</h2>
      <div className="cards">{group.map(({r})=><ResourceCard key={r.kind+':'+r.id} resource={r} href={to('ledger',r)}/>)}</div></section>})}
    {matches.length===0 && <p className="empty">لا توجد نتائج ضمن الحسابات المتاحة لك.</p>}
    {matches.length>size && <nav className="movement-pages" aria-label="صفحات الحسابات"><span>{(current-1)*size+1}–{Math.min(current*size,matches.length)} من {matches.length}</span><div>
      <button className="button" disabled={current===1} onClick={()=>setPage(current-1)}>السابق</button><button className="button" disabled={current*size>=matches.length} onClick={()=>setPage(current+1)}>التالي</button></div></nav>}
  </>;
}
