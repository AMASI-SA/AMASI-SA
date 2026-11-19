import React from 'react';
import { useOptionalAuth } from '../../context/AuthContext';
import { grantedWebLink, managedWebPages } from '../../lib/webPageAccess';
import './operationalToolTheme.css';

export default function OperationalToolBack() {
  const pages=managedWebPages(useOptionalAuth()?.user);
  const tools='/operational-balances?view=tools';
  const target=grantedWebLink(pages,tools)?tools:pages?.[0]?.to;
  if (!target) return null;
  return <nav className="mz-ops-back" aria-label="التنقل بين أدوات الأرصدة">
    <a href={target}>{target===tools?'العمليات والكشوف':'صفحات ميزان'}</a>
  </nav>;
}
