import React, { lazy, Suspense } from 'react';
import { useLocation } from 'react-router-dom';
import ApprovedSpecializedPage from './ApprovedSpecializedPage';

import Finance from './ApprovedFinance';
const Settings = lazy(() => import('./OperationalBalances'));
const PreviousWorkspace = lazy(() => import('./SimpleBalances'));

export default function ApprovedFinanceRoute() {
  const { pathname, search } = useLocation();
  const params = new URLSearchParams(search);
  const isDaily = pathname.endsWith('/daily-movements');
  const view = params.get('view');
  const oldDeepLink = isDaily && !view && ['tab','panel','dialog','action','focus_id','kind','id'].some(key=>params.has(key));
  return <Suspense fallback={<p dir="rtl" role="status">جارٍ فتح الأرصدة…</p>}>
    {view==='specialized' ? <ApprovedSpecializedPage><Settings key={search} /></ApprovedSpecializedPage> : oldDeepLink || view==='previous-workspace' ? <ApprovedSpecializedPage><PreviousWorkspace /></ApprovedSpecializedPage>
      : <Finance defaultView={isDaily?'log':'home'}/>}
  </Suspense>;
}
