import React, { cloneElement, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { grantedPageAt, grantedWebLink, managedWebPages } from '../../lib/webPageAccess';
import { ApprovedFinanceShell } from './ApprovedFinance';
import { Icon } from './ApprovedFinanceUI';
import { authIdentity } from './simpleBalancesHooks';
import './approvedSpecialized.css';

const base = '/operational-balances';
const pagesByPath = {
  [base + '/provider-fees']: ['تعرفات وعمولات المنصات', 'tools'],
  [base + '/provider-statements']: ['مطابقة كشوف منصات الدفع', 'tools'],
  [base + '/financial-position']: ['المركز المالي', 'position'],
  [base + '/customer-returns']: ['مرتجعات العملاء', 'tools'],
  [base + '/customer-exchanges']: ['استبدال الأصناف', 'tools'],
  [base + '/access']: ['صلاحيات الأرصدة', 'tools'],
  [base + '/shipping-review']: ['مراجعة أجور الشحن', 'reviews'],
  [base + '/provider-history']: ['مراجعة الطلبات السابقة', 'reviews'],
  '/shipping-settings-v2': ['شركات الشحن والتعرفات', 'tools'],
  '/inventory-v2': ['المخزون والمشتريات', 'tools'],
};
const sectionLabels = { home: 'الأرصدة التشغيلية', position: 'المركز المالي', reviews: 'تحتاج مراجعة', tools: 'العمليات والكشوف' };

export function specializedPage(location) {
  const found = pagesByPath[location.pathname];
  if (found) return { title: found[0], active: found[1] };
  return { title: 'الإعدادات والعمليات المتخصصة', active: 'tools' };
}

export function financeReturnTarget(value, pages, fallback) {
  // Only local finance routes can become a contextual return link. The same
  // page grant check used by navigation still applies after an actor change.
  if (typeof value === 'string' && /^\/operational-balances(?:[/?#]|$)/.test(value)) {
    const target = new URL(value, 'https://mezan.invalid');
    if (target.origin === 'https://mezan.invalid' && target.pathname.startsWith(base)
        && grantedWebLink(pages, value)) return value;
  }
  return grantedWebLink(pages, fallback) ? fallback : pages?.find(page => page.section === 'balances')?.to;
}

// Presentation only: the actual page and ProtectedRoute retain their existing
// scoped API, read/write and employee-reader boundaries. No additional balance
// request is needed just to display the shared navigation.
export default function ApprovedSpecializedPage({ children, title: givenTitle, active: givenActive, authenticatedUser }) {
  const location = useLocation();
  const { user: sessionUser } = useOptionalAuth() || {};
  const user = authenticatedUser || sessionUser;
  const pages = managedWebPages(user);
  const page = specializedPage(location);
  const title = givenTitle || page.title, active = givenActive || page.active;
  const context = { actor: { name: user?.name || user?.full_name || '',
    is_owner: user?.is_owner === true || String(user?.role || '').toLowerCase() === 'owner' } };
  const to = view => base + '?view=' + view;
  const returnTo = financeReturnTarget(location.state?.financeReturnTo, pages, to(active));
  const granted = grantedPageAt(pages, location);
  const content = location.pathname === '/inventory-v2' && React.isValidElement(children)
    ? cloneElement(children, { key: JSON.stringify([authIdentity({ user }), granted?.id, granted?.mode]), embedded: true,
      ...(granted?.id === 'products.inventory' ? { accessMode: granted.mode } : {}) }) : children;
  const returnLabel = returnTo && new URL(returnTo, 'https://mezan.invalid').searchParams.get('view') === 'ledger'
    ? 'العودة إلى كشف الحساب' : sectionLabels[active] || 'الأرصدة التشغيلية';
  useEffect(() => { const previous = document.title; document.title = title + ' · ميزان ٢'; return () => { document.title = previous; }; }, [title]);
  return <ApprovedFinanceShell context={context} user={user} title={title} active={active} view={active} to={to}>
    <div className="mz-specialized" data-finance-section={active}>
      {returnTo && <Link className="textbtn back mz-specialized-return" to={returnTo}><Icon name="chevron-left" />{returnLabel}</Link>}
      {content}
    </div>
  </ApprovedFinanceShell>;
}
