import http from '../../lib/api';
import {operationalApi} from './api';
jest.mock('../../lib/api', () => ({get: jest.fn(), post: jest.fn()}));
test('payroll context is a read-only GET with no implicit approval', async () => {
  http.get.mockResolvedValue({data: {first_day: '2026-11-15'}});
  expect(await operationalApi.salaryStartContext()).toEqual({first_day: '2026-11-15'});
  expect(http.get).toHaveBeenCalledWith('/operational-balances/salary-start/context');
  expect(http.post).not.toHaveBeenCalled();
});
test('preview passes the server cutoff unchanged', async () => {
  http.post.mockResolvedValue({data: {can_approve: true}});
  const body = {first_day: '2026-11-15'};
  await operationalApi.previewSalaryStart(body);
  expect(http.post).toHaveBeenCalledWith('/operational-balances/salary-start/preview', body);
});
test('approval preserves identity, source, scope and strict Boolean', async () => {
  http.post.mockResolvedValue({data: {ok: true}});
  const body = {request_id: 'same-request', first_day: '2026-11-15', expected_preview_hash: 'a'.repeat(64),
    expected_session_scope: 'b'.repeat(64), opening_salaries_excluded: true};
  await operationalApi.approveSalaryStart(body); await operationalApi.approveSalaryStart(body);
  expect(http.post.mock.calls).toEqual([['/operational-balances/salary-start/approve', body], ['/operational-balances/salary-start/approve', body]]);
});
