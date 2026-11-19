import React from 'react';
import { Link } from 'react-router-dom';
import { Amount, formatMinor, Heading, Icon, IconBox, MovementIcon, isAsset } from './ApprovedFinanceUI';
import { minorUnits, movementDate, movementRecorder, movementTitle, signedResourceBalance } from './simpleBalancesModel';
import { ResourceCard } from './ApprovedDirectory';

export default function ApprovedHome({context,to,onDetail,canAdd,allowedTo=()=>true}) {
  const cash=context.resources.filter(r=>['bank','cash'].includes(r.kind)&&!r.prepaid_shipping_cash&&r.currency==='SAR');
  const subtotals=kind=>{
    const visible=cash.filter(r=>r.kind===kind).map(r=>minorUnits(signedResourceBalance(r))).filter(v=>v!==null);
    return visible.length ? visible.reduce((a,b)=>a+b,0n) : null;
  };
  const bank=subtotals('bank'), box=subtotals('cash'), total=bank===null&&box===null?null:(bank??0n)+(box??0n);
  const accounts=context.resources.filter(r=>isAsset(r.kind));
  const preferred=['bank','cash','provider'].map(k=>accounts.find(r=>r.kind===k&&!r.prepaid_shipping_cash)).filter(Boolean);
  const cards=[...preferred,...accounts.filter(r=>!preferred.includes(r))].slice(0,3);
  const date=new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn',{dateStyle:'full',timeZone:'Asia/Riyadh'}).format(new Date());
  const recent=context.recent_movements;
  return <><Heading title="الأرصدة التشغيلية" subtitle={date}>{canAdd&&<Link className="button primary" to={to('entry')}><Icon name="plus"/>حركة مالية جديدة</Link>}</Heading>
    <div className="main-grid"><section className="hero"><div className="hero-caption">الرصيد المتاح في البنوك والصناديق</div>
      <div className="big"><bdi>{formatMinor(total)}</bdi>{total!==null && <span className="unit"> ريال سعودي</span>}</div>
      <div className="hero-sub">مجموع الحسابات النقدية المتاحة لك بالريال</div><div className="split">
      <div><span>البنوك</span><b><bdi>{formatMinor(bank)}</bdi></b></div><div><span>الصناديق النقدية</span><b><bdi>{formatMinor(box)}</bdi></b></div></div></section>
      <section className="panel quick"><h2>{canAdd?'وش صار للمبلغ؟':'حساباتي وحركاتي'}</h2><div className="quickrow">
      {(canAdd?[['in','استلمت مبلغ','arrow-down-left'],['out','دفعت مبلغ','arrow-up-right'],['move','حوّلت بين حسابين','arrow-left-right']]:
        [['assets','الأصول والحسابات','wallet'],['log','سجل الحركات','receipt-text'],['parties','الأشخاص والجهات','users']]).filter(([mode])=>canAdd||allowedTo(mode)).map(([mode,name,icon])=>
          <Link className="quickbtn" key={mode} to={canAdd?to('entry',null,{mode}):to(mode)}><IconBox name={icon} tone={'c-'+mode}/><span>{name}</span><span className="next"><Icon name="chevron-left" className="smallicon"/></span></Link>
        )}</div></section></div>
    <section className="section"><div className="section-head"><h2>حساباتي</h2>{allowedTo('assets')&&<Link className="textbtn" to={to('assets')}>كل الحسابات</Link>}</div>
      {cards.length?<div className="cards homecards">{cards.map(r=><ResourceCard key={r.kind+':'+r.id} resource={r} href={allowedTo('ledger',r)?to('ledger',r):null}/>)}</div>:<p className="empty">لا توجد حسابات متاحة لك.</p>}</section>
    <section className="section panel sheet"><div className="section-head"><h2>آخر الحركات المسجّلة</h2>{allowedTo('log')&&<Link className="textbtn" to={to('log')}>عرض السجل</Link>}</div>
      {recent?.length ? <div className="scroll"><table className="ledger home"><thead><tr><th>الحركة ومن سجّلها</th><th>من وإلى</th><th>المبلغ</th><th>الحالة</th><th>المرجع</th></tr></thead>
      <tbody>{recent.map(row=><tr key={row.event_id||row.id}><td><button className="tablelink" onClick={()=>onDetail(row)}><MovementIcon type={row.movement_type}/><span>{movementTitle(row)}<small>{movementDate(row)} · {movementRecorder(row)}</small></span></button></td>
      <td>{row.from_name && row.to_name ? <>{row.from_name} ← {row.to_name}</> : row.party_name}</td><td><Amount value={row.amount} currency={row.currency}/></td><td><span className="pill st-done">مسجّلة</span></td><td><bdi>{row.reference||row.order_number||'—'}</bdi></td></tr>)}</tbody></table></div>:
      <p className="empty">{Array.isArray(recent)?'لا توجد حركات مسجّلة متاحة لك.':'يمكنك فتح سجل الحركات لعرض التفاصيل.'}</p>}</section>
    {allowedTo('reviews')&&context.resources.some(r=>r.order_issues_count>0 || r.order_issues_count===null) && <Link className="section issue-notice" to={to('reviews')}><IconBox name="clock-3" tone="c-wait"/><strong>طلبات تحتاج مراجعة</strong><b>عرض الطلبات <Icon name="chevron-left"/></b></Link>}
  </>;
}
