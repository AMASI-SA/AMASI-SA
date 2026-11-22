import React, { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { formatRiyadhDateTime } from '../../lib/dates';
import { operationalApi } from './api';
import { assertWorkspace, retryWorkspaceRead, useWorkspaceRead } from './workspaceReadHooks';
import { Heading, Icon } from './ApprovedFinanceUI';
import { collectionLabel, currencyCodes, deliveryOrderLabel, destinationLabel, filterDeliveries, formatPositionCount, formatPositionMoney, isPositionContract, paymentLabel, readableMessages, UNAVAILABLE } from './financialPositionDisplay';
import { financialSnapshotKey, financialSnapshotParams, hasFinancialSnapshot, sameFinancialSnapshot } from './financialPositionViews';
import PaginatedReportTable from './PaginatedReportTable';
import BrandIcon from './BrandIcon';
import { brandForResource } from './brandIconModel';
import './approvedReports.css';

export const REPORT_TABS = [
  ['summary', 'الصورة الكاملة', 'summary'], ['profit', 'المبيعات والربح', 'summary'],
  ['providers', 'منصات الدفع والمرتجعات', 'providers'], ['accounts', 'البنوك والصناديق', 'accounts'],
  ['suppliers', 'الموردون', 'suppliers'], ['employees', 'الرواتب والعهد', 'employees'],
  ['couriers', 'الشحن والتحصيل', 'couriers'], ['drivers', 'الموصلون', 'drivers'],
  ['advertising', 'الإعلانات', 'advertising'], ['other', 'الجهات والمحافظ', 'other'],
  ['expected', 'المبالغ والتكاليف المحتملة', 'expected'], ['reconciliation', 'المطابقة والمصادر', 'summary'],
];
const list = value => Array.isArray(value) ? value : [];
const known = value => formatPositionMoney(value) !== UNAVAILABLE;
const currentLabel = { summary: 'الصورة المالية الحالية', profit: 'مبيعات الفترة وتكاليفها وربحها', expected: 'ما قد يدخل وما قد نتحمله لاحقًا' };
const error = message => Object.assign(new Error(message), { localMessage: message });

function validate(report, context, view, expected) {
  if (context.demandOnly) assertWorkspace(report, context);
  if (!isPositionContract(report) || report.permissions?.reports !== true || report.status !== 'active'
    || report.session_scope !== context.session_scope || report.view !== view || !hasFinancialSnapshot(report)
    || expected && !sameFinancialSnapshot(expected, report)) {
    throw error('تعذر التحقق من لقطة التقرير. أعد قراءة التقرير لعرض بيانات متسقة لهذا الحساب.');
  }
  return { ...report };
}
function Money({ value, currency }) {
  const amount = formatPositionMoney(value);
  return <span className={'report-money' + (typeof value === 'string' && value.startsWith('-') ? ' negative' : '')}>
    {amount === UNAVAILABLE ? <span className="report-unavailable">غير متاح</span> : <><bdi dir="ltr">{amount}</bdi> <small>{currency === 'SAR' ? 'ر.س' : currency}</small></>}
  </span>;
}
function Notes({ items }) {
  const notes = readableMessages(items);
  return notes.length ? <details className="report-notes"><summary>المصدر وحدود هذه الأرقام</summary><ul>{notes.map(note => <li key={note}>{note}</li>)}</ul></details> : null;
}
function Metric({ title, value, subtotal, currency, note, featured = false, partial = false }) {
  return <article className={'report-metric panel' + (featured ? ' report-featured' : '')}>
    <h3>{title}</h3><strong><Money value={value} currency={currency}/></strong>
    {!known(value) && known(subtotal) && <p className="report-partial">الجزء المعلوم فقط: <Money value={subtotal} currency={currency}/></p>}
    {partial && known(value) && <span className="report-badge">الجزء المسجل؛ التغطية غير مكتملة</span>}
    {note && <p>{note}</p>}
  </article>;
}
function ValueRows({ title, rows, currency, id }) {
  return <section className="panel report-section" aria-label={title} data-testid={id}>
    <div className="section-head"><h2>{title}</h2></div>
    {!list(rows).length ? <p className="empty">لم تتوفر بنود هذا التقرير.</p> : <div className="report-value-rows">
      {rows.map(row => <div key={row.id} className={row.emphasis === 'total' ? 'report-total' : ''}>
        <div><strong>{row.label}</strong>{row.reason && <p>{row.reason}</p>}
          {!known(row.amount) && known(row.known_subtotal) && <small>الجزء المعلوم فقط: <Money value={row.known_subtotal} currency={currency}/></small>}
        </div><div><Money value={row.complete === false ? null : row.amount} currency={currency}/>
          {row.complete === false && <small className="report-badge">غير مكتمل</small>}</div>
      </div>)}
    </div>}
  </section>;
}
function PendingDetails({ row }) {
  const orders = list(row.pending_order_details);
  const count=Number.isSafeInteger(row.pending_orders)?row.pending_orders:null;
  const missing=count!==null && count>orders.length;
  return orders.length || count>0 ? <details className="report-order-details"><summary>فتح مراجعة الطلبات</summary>
    <p>تفاصيل من لقطة التقرير نفسها؛ لا تتغير الأرصدة عند فتحها.</p>
    {orders.length>0 && <PaginatedReportTable rows={orders} label={'مراجعة ' + (row.name || 'الطلبات')} pageSize={8}>{visible=><ul>
      {visible.map((order, index) => <li key={order.order_id || index}><strong>{order.order_number || order.order_id || 'طلب غير محدد'}</strong>
        {readableMessages(order.reasons).length ? readableMessages(order.reasons).map(reason => <p key={reason}>{reason}</p>) : <p>سبب المراجعة غير متاح في هذه اللقطة.</p>}</li>)}</ul>}</PaginatedReportTable>}
    {missing && <p className="report-partial">بعض طلبات المراجعة لا تتوفر لها تفاصيل مرتبطة بمعرّف في هذه اللقطة.</p>}
  </details> : null;
}
function Cell({ row, column, currency, tableId }) {
  const value = row[column.key];
  if (column.type === 'money') return <><Money value={value} currency={currency}/>
    {(column.key === 'estimated_fees' && row.estimated_fees_complete === false || row.complete === false) && known(value) && <small className="report-partial">جزء معلوم</small>}</>;
  if (['count', 'exceptions'].includes(column.type)) return <><bdi>{formatPositionCount(value)}</bdi>{column.type === 'exceptions' && tableId && <PendingDetails row={row}/>}</>;
  const kind = {providers:'provider',banks_network:'bank',ads:'ad_account',couriers:'courier'}[tableId];
  if (column.key==='name' && kind && typeof value==='string') return <span className="report-party"><BrandIcon size="sm" brand={brandForResource({kind,id:row.provider||row.party_id||row.id,name:value,platform:row.platform,bank_code:row.bank_code})}/>{value}</span>;
  return typeof value === 'string' && value.trim() ? value : UNAVAILABLE;
}
function SourceTable({ table, currency, snapshot }) {
  const columns = list(table.columns);
  const rows = list(table.rows);
  return <section className="panel report-section" data-testid={'report-table-' + table.id}>
    <div className="section-head"><h2>{table.title}</h2><span className={'report-badge ' + (table.complete === true ? 'complete' : '')}>
      {table.complete === true ? table.kind === 'forecast' ? 'تقدير مكتمل النطاق' : 'مكتمل ضمن المصدر' : 'تغطية غير مكتملة'}</span></div>
    {table.kind === 'forecast' && <p className="report-caption">مبالغ محتملة؛ ليست نقدًا مستلمًا ولا تضاف إلى الأرصدة المؤكدة.</p>}
    {table.complete !== true && <p className="report-caption">التغطية غير مكتملة؛ المبالغ الظاهرة تخص الأجزاء المعلومة. «غير متاح» لا يعني صفرًا.</p>}
    {table.totals_basis === 'known_subtotals' && <p className="report-caption">الإجماليات أدناه تجمع الجزء المعلوم فقط؛ ليست إجماليًا نهائيًا.</p>}
    <PaginatedReportTable rows={rows} label={table.title} resetKey={snapshot + currency} pageSize={12}>{pageRows => <div className="report-scroll" tabIndex={0} role="region" aria-label={table.title}>
      <table><thead><tr>{columns.map(column => <th key={column.key} scope="col">{column.label}</th>)}</tr></thead>
        <tbody>{pageRows.map((row, index) => <tr key={row.id || index}>{columns.map(column => <td key={column.key}><Cell row={row} column={column} currency={currency} tableId={table.id}/></td>)}</tr>)}</tbody>
        {table.show_totals !== false && Object.keys(table.totals || {}).length > 0 && <tfoot><tr>{columns.map((column, index) => <td key={column.key}>
          {index === 0 ? table.totals_basis === 'known_subtotals' ? 'مجموع الجزء المعلوم' : 'إجمالي المصدر' : <Cell row={table.totals} column={column} currency={currency}/>}</td>)}</tr></tfoot>}
      </table>{!rows.length && <p className="empty">{table.complete === true ? 'لا توجد بنود ضمن نطاق هذا المصدر.' : 'لا توجد تفاصيل موثقة كافية؛ لا يعني ذلك أن المبلغ صفر.'}</p>}
    </div>}</PaginatedReportTable>
    <Notes items={table.notes}/>
  </section>;
}
function DeliveryTables({ deliveries, currency, snapshot }) {
  if (!deliveries) return null;
  const rows=filterDeliveries(list(deliveries.rows)).map(row=>({...row,
    order_label:deliveryOrderLabel(row),payment_label:paymentLabel(row.payment_method),
    collection_label:collectionLabel(row.collection_status),destination_label:destinationLabel(row.collection_destination)}));
  const field=(key,label,type='money')=>({key,label,type});
  const base={kind:'actual',complete:deliveries.complete,rows,show_totals:false,notes:deliveries.notes};
  const identity=[field('order_label','الطلب','text'),field('party_name','الجهة','text')];
  return <><SourceTable currency={currency} snapshot={snapshot} table={{...base,id:'delivery_shipping',title:'أجور الشحن حسب الطلب',
    columns:[...identity,field('shipping_confirmed','الأجرة المثبتة'),field('shipping_settled','المسدد من الأجرة'),field('shipping_outstanding','المتبقي من الأجرة')]}}/>
    <SourceTable currency={currency} snapshot={snapshot} table={{...base,id:'delivery_collections',title:'التحصيل والمسؤولية المثبتة حسب الطلب',
      columns:[...identity,field('payment_label','طريقة الدفع','text'),field('collection_label','حالة التحصيل','text'),
        field('destination_label','وجهة التحصيل','text'),field('courier_responsibility','ذمة شركة الشحن المثبتة'),
        field('collection_amount','مبلغ التحصيل في المصدر'),field('driver_custody','عهدة الموصل'),
        field('collection_settled','السداد المخصص للتحصيل'),field('collection_outstanding','المتبقي لدى المنفذ'),
        field('store_collection','تحصيل مباشر للمتجر'),field('customer_outstanding','المتبقي وفق الطلب')]}}/></>;
}
function DebtTable({ figures, currency }) {
  return <SourceTable currency={currency} snapshot="summary" table={{ id:'debts', title:'كم لنا وكم علينا حسب الجهة', complete:figures.completeness?.actual,
    columns:[{key:'label',label:'الجهة',type:'text'},{key:'receivable',label:'لنا',type:'money'},{key:'payable',label:'علينا',type:'money'}],
    rows:list(figures.debt_rows).map(row=>({...row,label:row.label || row.name})), totals:figures.debt_totals,
    notes:['الحقوق والالتزامات ليست نقدًا متاحًا. لا تُجمع هذه التفاصيل مرة أخرى مع إجمالي المركز المالي.'] }}/>
}
function Cohort({ figures }) {
  const sales = list(figures.profit_rows).find(row => row.id === 'net_sales');
  const coverage = sales?.coverage || {};
  const counts = [['orders','طلبات مجموعة المبيعات'],['financial_eligible_orders','المؤهلة ماليًا'],['excluded_unpaid_orders','مستبعدة لعدم الدفع'],['pending_orders','تحتاج إثبات نطاق الفترة'],['review_orders','تحتاج مراجعة الأهلية']];
  return <div className="report-counts">{counts.filter(([key])=>Object.hasOwn(coverage,key)).map(([key,label])=><div key={key}><small>{label}</small><strong>{formatPositionCount(coverage[key])}</strong></div>)}</div>;
}
function Reconciliation({ figures }) {
  return <><section className="panel report-section"><div className="section-head"><h2>مطابقة سلة مع ميزان</h2><span className="report-badge">لم تصدر شهادة مطابقة</span></div>
    <p>تقرير الربح يستخدم مجموعة الطلبات المثبتة في المصدر للفترة المعروضة. هذا لا يثبت وحده تطابق إجمالي لوحة سلة أو اكتمال جميع المرتجعات.</p>
    <Cohort figures={figures}/><div className="report-checklist">
      <p><Icon name="calendar-days"/>يجب أن تتطابق الفترة والمنطقة الزمنية ومجموعة الطلبات والعملات بين المصدرين.</p>
      <p><Icon name="receipt-text"/>مرتجع المنتجات ورد النقد وتسوية المنصة أحداث مختلفة؛ لا تخصم قيمة المرتجع مرتين.</p>
      <p><Icon name="shield-check"/>لا يظهر حكم «مطابق» قبل وجود مقارنة موثقة على مستوى الطلب والدفع والاسترداد.</p>
    </div><p className="report-caption">المقارنة الشاملة المستقلة بين سلة وميزان غير متاحة في عقد هذا التقرير حاليًا.</p>
  </section><section className="panel report-section"><h2>تابع الأدلة من مصدرها</h2><div className="report-source-links">
    <Link to="/operational-balances?view=issues">الطلبات التي تحتاج مراجعة <Icon name="chevron-left"/></Link>
    <Link to="/operational-balances/provider-statements">كشوف المنصات ومطابقتها <Icon name="chevron-left"/></Link>
    <Link to="/operational-balances/customer-returns">مرتجعات الأصناف <Icon name="chevron-left"/></Link>
    <Link to="/operational-balances?view=refund">طلبات رد المبالغ وأدلتها <Icon name="chevron-left"/></Link>
  </div></section></>;
}

// All values are server projections of one saved snapshot. This page neither
// refreshes sources nor recalculates fees, balances, refunds or profit.
export default function ApprovedReports({ context, identity }) {
  const location = useLocation(), navigate = useNavigate();
  const query = new URLSearchParams(location.search);
  const selected = REPORT_TABS.find(([key]) => key === query.get('report_tab')) || REPORT_TABS[0];
  const [tab, title, view] = selected;
  const [chosenCurrency, setCurrency] = useState('');
  const [revoked, setRevoked] = useState(null);
  const enabled = context.actor?.is_owner === true && context.status === 'active';
  const scope = JSON.stringify([identity, context.session_scope, context.permission_version]);
  const summary = useWorkspaceRead(async config => validate(await operationalApi.financialPosition({...config,params:{view:'summary'}}),context,'summary'),scope,enabled);
  const snapshot = summary.data;
  const snapshotKey = financialSnapshotKey(snapshot);
  const detail = useWorkspaceRead(async config => validate(await operationalApi.financialPosition({...config,
    params:{view,...financialSnapshotParams(snapshot)}}),context,view,snapshot),JSON.stringify([scope,snapshotKey,view]),enabled && Boolean(snapshot) && view !== 'summary');
  const detailDenied = [401,403].includes(detail.status);
  useEffect(()=>{if(detailDenied && snapshot)setRevoked({scope,snapshot});},[detailDenied,scope,snapshot]);
  // Switching tabs must not redisplay cached owner figures after a revoked
  // permission. Only a fresh successful summary read can clear this boundary.
  const denied = detailDenied || revoked?.scope === scope && revoked?.snapshot === snapshot;
  const retry = () => {
    const failed = detail.error ? detail : summary;
    if ([401,403,409].includes(failed.status) && context.reloadWorkspace) retryWorkspaceRead(failed,context);
    else summary.reload();
  };
  const report = denied ? null : view === 'summary' ? snapshot : detail.data;
  const codes = currencyCodes(report || snapshot);
  const currency = codes.includes(chosenCurrency) ? chosenCurrency : codes.includes('SAR') ? 'SAR' : codes[0];
  const figures = report?.currencies?.[currency];
  const changeTab = key => { const params = new URLSearchParams(location.search); params.set('report_tab',key); navigate({...location,search:'?' + params}); };
  const failure = summary.error || detail.error || (denied ? 'توقفت صلاحية قراءة التقرير. أعد القراءة بعد التحقق من صلاحياتك.' : '');
  return <div className="approved-reports">
    <Heading title="التقارير" subtitle="الأرصدة، الحقوق، الالتزامات، والتكاليف والربح من مصادر ميزان الفعلية.">
      {enabled && <button className="button" type="button" onClick={retry} disabled={summary.loading}><Icon name="refresh-cw"/>إعادة قراءة التقرير</button>}
    </Heading>
    {!context.actor?.is_owner ? <p role="alert" className="note">التقارير المالية الشاملة متاحة لمالك الحساب.</p>
      : !enabled ? <p className="note">تتاح التقارير بعد اعتماد الأرصدة الافتتاحية وبدء التشغيل.</p>
      : <>
        {snapshot && !denied && <div className="report-scope panel"><div><b>فترة التقرير</b><p>من {formatRiyadhDateTime(snapshot.started_at)} إلى {formatRiyadhDateTime(snapshot.as_of)} · الرياض</p><small>لقطة محفوظة؛ إعادة القراءة لا تحدّث سلة ولا تسجّل حركة مالية.</small></div>
          <label>عملة التقرير<select value={currency || ''} onChange={event=>setCurrency(event.target.value)}>{codes.map(code=><option key={code}>{code}</option>)}</select></label></div>}
        <nav className="report-tabs" aria-label="أقسام التقارير">{REPORT_TABS.map(([key,label])=><button type="button" key={key} aria-current={tab===key?'page':undefined} className={tab===key?'on':''} onClick={()=>changeTab(key)}>{label}</button>)}</nav>
        {failure && <div role="alert" className="note warning"><p>{failure}</p><button className="textbtn" type="button" onClick={retry}>إعادة قراءة لقطة متسقة</button></div>}
        {!failure && !report && <p className="empty" role="status">جارٍ قراءة التقرير…</p>}
        {report && !figures && <p className="empty">لا توجد بيانات موثقة لهذه العملة في هذا القسم.</p>}
        {figures && <>
          <div className="report-section-title"><h2>{currentLabel[tab] || title}</h2><span>{currency}</span></div>
          {tab==='summary' && <>
            {figures.completeness?.actual !== true && <p className="note warning report-coverage">الأرصدة المعروضة تخص الجزء المسجل؛ تغطية المصادر غير مكتملة. لا تعتبر المجاميع الجزئية حصرًا نهائيًا لكل ما لنا وما علينا.</p>}
            <div className="report-metrics"><Metric title="أرصدة البنوك والصناديق" value={figures.metrics?.liquidity} currency={currency} note="تشمل الأرصدة المخصصة إن وجدت؛ رصيد الشحن المقدم ليس نقدًا حرًا"/>
              <Metric title="حقوق مسجلة لنا" value={figures.debt_totals?.receivable} subtotal={figures.known_debt_totals?.receivable} currency={currency} partial={figures.completeness?.actual!==true} note="ليست كلها نقدًا مستلمًا"/>
              <Metric title="التزامات مسجلة علينا" value={figures.debt_totals?.payable} subtotal={figures.known_debt_totals?.payable} currency={currency} partial={figures.completeness?.actual!==true} note="المستحق حسب المصادر المتاحة"/>
              <Metric title="المركز النقدي والديون" value={figures.metrics?.monetary_position} subtotal={figures.metrics?.known_monetary_position} currency={currency} featured note="النقد + الحقوق − الالتزامات؛ ليس صافي الربح"/></div>
            <ValueRows title="تفصيل المركز المالي" rows={list(figures.current_rows).map(row=>row.id==='liquidity'?{...row,label:'أرصدة البنوك والصناديق حسب المصدر',reason:'أرصدة الشحن المخصصة ليست نقدًا حرًا للاستخدام.'}:row)} currency={currency}/><DebtTable figures={figures} currency={currency}/>
            <div className="report-next panel"><div><h3>الأموال المحتملة والربح لهما حساب مستقل</h3><p>افتح التوقعات للتكاليف والتحصيل المحتمل، أو الربح لنتيجة مبيعات الفترة بعد عناصر تكلفتها.</p></div>
              <button type="button" className="button" onClick={()=>changeTab('expected')}>المبالغ المحتملة</button><button type="button" className="button" onClick={()=>changeTab('profit')}>المبيعات والربح</button></div>
          </>}
          {tab==='profit' && <><Cohort figures={figures}/><div className="report-metrics report-two"><Metric title="ربح الفترة قبل التمويل والزكاة والضريبة" value={figures.metrics?.operating_profit} currency={currency} note="يعرض فقط عند اكتمال مكونات المجموعة والفترة"/>
            <Metric title="صافي الربح" value={figures.metrics?.net_profit} currency={currency} featured note="لا يستنتج من أرصدة البنوك أو تمويل المالك"/></div>
            <ValueRows title="كيف تكوّنت نتيجة الفترة؟" rows={figures.profit_rows} currency={currency} id="report-profit"/>
            <p className="note">المبيعات هنا وفق أساس الطلبات في المصدر؛ لا تعني المبلغ المحصل بالبنك، ولا توصف بأنها مبيعات دون ضريبة إلا إذا أثبت المصدر ذلك.</p></>}
          {tab==='reconciliation' && <Reconciliation figures={figures}/>}
          {tab==='expected' && <><p className="note">احتمال التحصيل لا يثبت استلام النقد. اعتماد طلب الدفع عند الاستلام أو تجهيزه لا يضمن قبوله من العميل؛ تُعرض الالتزامات المثبتة مستقلة عن توقع تحصيله.</p>
            <ValueRows title="ملخص البنود المتوقعة" rows={figures.forecast_rows} currency={currency}/></>}
          {tab==='providers' && <p className="note">الاستردادات هنا تخص أدلة منصات الدفع المعروضة. ليست إجمالي جميع مرتجعات المتجر؛ لا تُخصم ثانية من صافي المنصة أو مبيعات الفترة.</p>}
          {['couriers','drivers'].includes(tab) && <p className="note">ذمة التحصيل لدى شركة الشحن أو عهدة الموصل لا تعني استلام المبلغ في البنك. أجور الشحن والسداد المخصص يظهران مستقلين؛ لا تنشأ مقاصة تلقائية دون تسوية مثبتة.</p>}
          {view!=='summary' && (!list(figures.tables).length ? <p className="empty">لا تتوفر تفاصيل موثقة لهذا القسم.</p> : figures.tables.map(table=><SourceTable key={table.id} table={table} currency={currency} snapshot={snapshotKey}/>))}
          {['couriers','drivers'].includes(tab) && <DeliveryTables deliveries={figures.deliveries} currency={currency} snapshot={snapshotKey}/>}
          <Notes items={[...list(figures.notes),...list(report.issues),...list(report.dependencies).filter(item=>!['available','ready','complete','active'].includes(item.status))]}/>
        </>}
      </>}
  </div>;
}
