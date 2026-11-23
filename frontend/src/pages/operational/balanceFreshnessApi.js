import http from '../../lib/api';
import { operationalApi } from './api';
import { refreshVisibleSnapshot } from './visibleOperationalRead';
import { createBalanceFreshnessManager } from './visibleBalanceFreshness';

export const readBalanceFreshness = config => http.get('/operational-balances/freshness', {
  timeout: 15000, ...config,
}).then(response => response.data);

export const balanceFreshnessManager = createBalanceFreshnessManager({
  read: readBalanceFreshness,
  refresh: config => refreshVisibleSnapshot(operationalApi, config),
});
