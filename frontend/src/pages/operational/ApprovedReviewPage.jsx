import React, { lazy, Suspense } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { authIdentity, useBalanceRead } from './simpleBalancesHooks';
import { simpleBalancesApi } from './simpleBalancesApi';
import { validContext } from './simpleBalancesModel';
import { Heading } from './ApprovedFinanceUI';

const ShippingReview = lazy(() => import('./CompletedShippingFeeReview'));
const ProviderHistory = lazy(() => import('./ProviderHistoryReview'));

export default function ApprovedReviewPage({ kind }) {
  const auth = useOptionalAuth(), location = useLocation();
  const identity = authIdentity(auth);
  const owner = auth?.user?.is_owner === true || String(auth?.user?.role || '').toLowerCase() === 'owner';
  const read = useBalanceRead(async config => {
    const context = await simpleBalancesApi.context(config, { homePreview: false, directoryOnly: true });
    if (!validContext(context) || context.actor.id !== auth.user.id || context.actor.is_owner !== true) throw new Error('invalid review context');
    return context;
  }, identity, owner && Boolean(auth?.user?.id) && !auth?.loading);
  const title = kind === 'shipping' ? 'مراجعة أجور الشحن' : 'مراجعة الطلبات السابقة';
  const providerId = new URLSearchParams(location.search).get('provider') || undefined;
  return <><Heading title={title} subtitle="راجع دليل المصدر وأثره قبل اعتماد أي تغيير."><Link className="button" to="/operational-balances/financial-position">المركز المالي وتحديث الأرصدة</Link></Heading>
    {!owner ? <p role="alert">هذه المراجعة متاحة للمالك فقط.</p>
      : read.error ? <div role="alert" className="note">{read.error}<button className="button" onClick={read.reload}>إعادة المحاولة</button></div>
      : !read.data ? <p role="status">جارٍ قراءة نطاق الأرصدة…</p>
      : read.data.status !== 'active' ? <p className="note">تتاح المراجعة بعد بدء الأرصدة التشغيلية.</p>
      : <Suspense fallback={<p role="status">جارٍ فتح المراجعة…</p>}>
        {kind === 'shipping' ? <ShippingReview key={identity + read.data.session_scope} sessionScope={read.data.session_scope} />
          : <ProviderHistory key={identity + read.data.session_scope} sessionScope={read.data.session_scope} providerId={providerId} />}
      </Suspense>}
  </>;
}
