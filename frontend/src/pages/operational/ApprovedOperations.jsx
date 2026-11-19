import React, { useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { formatRiyadhArabicLong } from '../../lib/dates';
import { grantedWebLink } from '../../lib/webPageAccess';
import { operationalApi } from './api';
import { useBalanceRead } from './simpleBalancesHooks';
import { minorUnits, resourceKey, signedResourceBalance } from './simpleBalancesModel';
import { Amount, formatMinor, Heading, Icon, IconBox, assetGroups, partyGroups, isAsset, styleOf } from './ApprovedFinanceUI';
import './approvedOperations.css';

const base = '/operational-balances';
const kindOrder = [...assetGroups, ...partyGroups].flatMap(([, kinds]) => kinds);
const positionView = view => base + '/financial-position?view=' + view;
const specialist = section => base + '?view=specialized&section=' + section;
const tools = [
  ['منصات الدفع والشبكة', [
    ['رفع كشف منصة ومطابقته', base + '/provider-statements#upload', 'file-text'],
    ['سجل الكشوف المرفوعة', base + '/provider-statements#history', 'history'],
    ['تعرفات وعمولات المنصات', base + '/provider-fees', 'coins'],
    ['أرصدة منصات الدفع', positionView('providers'), 'credit-card'],
    ['تسويات الشبكة والبنوك والصناديق', positionView('accounts'), 'landmark'],
  ]],
  ['الشحن والتحصيل', [
    ['شركات الشحن وأجور الشحنات', positionView('couriers'), 'package-check'],
    ['تحصيلات الموصلين وعهدهم', positionView('drivers'), 'truck'],
    ['تعرفات شركات الشحن', '/shipping-settings-v2?from=operational-tools', 'sliders-horizontal'],
    ['الطلبات المتوقعة وغير المنسوبة لجهة', positionView('expected'), 'clipboard-list'],
  ]],
  ['الطلبات والمخزون', [
    ['مرتجعات الأصناف', base + '/customer-returns', 'corner-down-left'],
    ['استبدال الأصناف', base + '/customer-exchanges', 'arrow-left-right'],
    ['المخزون المتاح', '/inventory-v2?tab=stock', 'store'],
    ['شراء مخزون', '/inventory-v2?tab=purchase', 'shopping-bag'],
    ['فواتير شراء المخزون', '/inventory-v2?tab=purchases', 'receipt-text'],
    ['فواتير الموردين ومطابقتها', positionView('suppliers'), 'briefcase-business'],
  ]],
  ['الأرصدة والإعدادات', [
    ['الأرصدة الافتتاحية', specialist('openings'), 'vault', 'draft'],
    ['المخزون الافتتاحي', specialist('opening-stock'), 'package-check'],
    ['بداية الرواتب', specialist('salary-start'), 'calendar-days', 'active'],
    ['الموظفون والعهد', positionView('employees'), 'users'],
    ['أرصدة الإعلانات وتحويل العملات', positionView('advertising'), 'megaphone'],
    ['محافظ العملاء والجهات الأخرى', positionView('other'), 'wallet'],
    ['العمليات المتخصصة ومرتجعات الموردين', specialist('movements'), 'layers'],
    ['التقرير التفصيلي وسجل التدقيق', specialist('reports'), 'notebook-pen'],
    ['صلاحيات الموظفين', base + '/access', 'shield-check', 'access'],
  ]],
];

function ToolGroup({ name, links }) {
  if (!links.length) return null;
  return <section className="section"><h2 className="card-grid-title">{name}</h2><div className="cards mz-tools-grid">
    {links.map(([title, to, icon]) => <Link to={to} className="resource mz-tool-link" key={to}>
      <IconBox name={icon} tone="party" /><strong>{title}</strong><Icon name="chevron-left" />
    </Link>)}
  </div></section>;
}

export default function ApprovedOperations({ context, onBack, pages=null }) {
  const owner = context.actor?.is_owner === true;
  const workspace = useLocation().pathname;
  const grants = new Set((context.operations || []).map(operation => typeof operation === 'string' ? operation : operation.key));
  const employeeLinks = [];
  if (grants.has('customer.refund_request') || grants.has('customer.refund_execute')) {
    employeeLinks.push(['رد مبلغ برقم الطلب', workspace + '?view=refund', 'corner-down-left']);
  }
  if (grants.has('courier.review') && context.resources.some(r => r.kind === 'store_driver' && r.access?.movements === 'all')) {
    employeeLinks.push(['مراجعة تحصيلات الموصلين', workspace + '?view=collections', 'list-checks']);
  }
  const visibleGroups = tools.map(([name, links]) => [name, links.filter(([,to,,requirement]) =>
    (owner || pages && grantedWebLink(pages,to) && (!to.endsWith('#upload') || pages.some(page=>page.id==='balances.statements'&&page.mode==='write'))) && (requirement === 'access' ? context.capabilities?.manage_access === true : !requirement || context.status === requirement))]);
  const visibleEmployees = employeeLinks.filter(([,to])=>grantedWebLink(pages,to));
  return <>
    <button type="button" className="textbtn back" onClick={onBack}><Icon name="chevron-left" />الأرصدة التشغيلية</button>
    <Heading title="العمليات والكشوف" />
    {visibleGroups.map(([name, links]) => <ToolGroup key={name} name={name} links={links} />)}
    <ToolGroup name="عمليات الطلبات" links={visibleEmployees} />
    {!owner && !visibleEmployees.length && !visibleGroups.some(([,links])=>links.length) && <p className="empty">لا توجد عمليات إضافية متاحة ضمن صلاحياتك.</p>}
  </>;
}

function PositionLine({ resource, onOpen, enabled=true }) {
  const [type, icon, tone] = styleOf(resource);
  const isAccount = isAsset(resource.kind);
  const signed = signedResourceBalance(resource);
  const description = !isAccount && signed !== null ? { for_party: 'له عند المؤسسة', for_us: 'عليه للمؤسسة', balanced: 'الحساب متوازن' }[resource.balance.side] : '';
  let amount = signed;
  if (signed !== null && !['bank', 'cash'].includes(resource.kind)) {
    amount = resource.balance.amount;
    if (isAccount && resource.balance.side === 'for_party') amount = '-' + amount;
  }
  if (resource.kind === 'ad_account') amount = resource.access?.balance === true ? resource.ad_wallet_balance : null;
  return <button type="button" className="review-item mz-position-line" disabled={!enabled} onClick={() => onOpen(resource)}>
    <IconBox name={icon} tone={tone} />
    <span>{resource.name}<small className="under">{resource.kind === 'ad_account' ? 'رصيد المحفظة الإعلانية' : type}</small></span>
    <strong className="end"><Amount value={amount} currency={resource.currency}/> {description && <small>{description}</small>}</strong>
  </button>;
}

function PositionMetric({ title, amount, currency, partial }) {
  return <div className="metric"><small>{title}</small><strong><Amount value={amount} currency={currency}/></strong>
    {partial && <small>الأرصدة المثبتة فقط</small>}</div>;
}

export function ApprovedPosition({ context, identity, onBack, onOpen, scoped=false, canOpen=()=>true }) {
  const owner = context.actor?.is_owner === true;
  const [selectedCurrency, setSelectedCurrency] = useState('');
  const [page, setPage] = useState(1);
  const resources = useMemo(() => context.resources.filter(resource => resource.access?.balance === true)
    .sort((a, b) => kindOrder.indexOf(a.kind) - kindOrder.indexOf(b.kind)), [context.resources]);
  const read = useBalanceRead(async config => {
    const report = await operationalApi.financialPosition({ ...config, params: { view: 'summary' } });
    if (report?.permissions?.reports !== true || report.session_scope !== context.session_scope || report.view !== 'summary' || report.schema_version !== 1 ||
        report.authority !== 'operational_balance_report' || !report.currencies || Array.isArray(report.currencies)) {
      throw Object.assign(new Error('Invalid financial summary'), { localMessage: 'تعذرت قراءة الملخص المالي لهذا الحساب.' });
    }
    return report;
  }, JSON.stringify([identity, context.session_scope, context.permission_version, 'position']), owner && context.status === 'active');
  if (!owner && !scoped) return <p role="alert">المركز المالي متاح لمالك الحساب.</p>;
  const report = read.data;
  const currencies = Object.keys(report?.currencies || {});
  const currency = currencies.includes(selectedCurrency) ? selectedCurrency : currencies.includes('SAR') ? 'SAR' : currencies[0];
  const figures = report?.currencies?.[currency];
  const pageSize = 24, pages = Math.max(1, Math.ceil(resources.length / pageSize));
  const activePage = Math.min(page, pages);
  const from = (activePage - 1) * pageSize;
  // Prepaid shipping credit is spendable only on shipping, not available bank or till cash.
  const spendable = resources.filter(r => ['bank', 'cash'].includes(r.kind) && !r.prepaid_shipping_cash && r.currency === currency);
  const cashAmounts = spendable.map(r => minorUnits(signedResourceBalance(r)));
  const availableCash = cashAmounts.every(n => n !== null) ? cashAmounts.reduce((total, n) => total + n, 0n) : null;
  const date = report?.as_of && Number.isFinite(Date.parse(report.as_of)) ? formatRiyadhArabicLong(report.as_of) : '';
  return <>
    <button type="button" className="textbtn back" onClick={onBack}><Icon name="chevron-left" />الأرصدة التشغيلية</button>
    <Heading title="المركز المالي" subtitle={scoped ? 'أرصدة الحسابات والجهات المسموح لك بعرضها' : date ? 'ملخص المالك في ' + date : 'ملخص المالك'}>
      {owner&&<Link to={positionView('summary')} className="textbtn">التقرير التفصيلي <Icon name="chevron-left" /></Link>}</Heading>
    {!scoped && (context.status !== 'active' ? <p className="empty">يتاح ملخص المركز المالي أثناء التشغيل بعد اعتماد الأرصدة الافتتاحية.</p>
      : read.error ? <div role="alert" className="note warning">{read.error}{' '}<button type="button" className="textbtn" onClick={read.reload}>إعادة المحاولة</button></div>
      : !report ? <p role="status">جارٍ قراءة ملخص الأرصدة…</p> : !figures ? <p className="empty">لا توجد بيانات عملة متاحة لهذا الملخص.</p>
      : <>
        {currencies.length > 1 && <label className="mz-position-currency">عملة الملخص <select value={currency} onChange={event => setSelectedCurrency(event.target.value)}>
          {currencies.map(code => <option key={code}>{code}</option>)}</select></label>}
        <div className="cards"><div className="metric"><small>المتاح في البنوك والصناديق</small><strong><bdi>{formatMinor(availableCash)}</bdi> {availableCash !== null && <span className="unit">{currency === 'SAR' ? 'ر.س' : currency}</span>}</strong></div>
          <PositionMetric title="مبالغ عند منصات الدفع" amount={figures.debt_rows?.find(row => row.id === 'providers')?.receivable}
            currency={currency} partial={figures.completeness?.actual === false} />
          <PositionMetric title="مبالغ مستحقة للجهات" amount={figures.debt_totals?.payable} currency={currency}
            partial={figures.completeness?.actual === false && figures.debt_totals?.payable != null} /></div></>)}
    <section className="section panel sheet"><div className="section-head"><h2>تفاصيل الأرصدة</h2></div>
      {resources.slice(from, from + pageSize).map(resource => <PositionLine key={resourceKey(resource)} resource={resource} onOpen={onOpen} enabled={canOpen(resource)} />)}
      {pages > 1 && <nav className="movement-pages" aria-label="صفحات تفاصيل الأرصدة">
        <span>عرض {from + 1}–{Math.min(from + pageSize, resources.length)} من {resources.length}</span><div>
          <button type="button" className="button" onClick={() => setPage(activePage - 1)} disabled={activePage === 1}>السابق</button>
          <span>صفحة {activePage} من {pages}</span><button type="button" className="button" onClick={() => setPage(activePage + 1)} disabled={activePage === pages}>التالي</button>
        </div></nav>}
      {!resources.length && <p className="empty">لا توجد حسابات متاحة للعرض.</p>}
    </section>
  </>;
}
