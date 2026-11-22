import api from '../lib/api';
import { getProviderStatementAccess, providerStatementAccessPath, saveProviderStatementAccess } from './providerStatementAccess';

jest.mock('../lib/api', () => ({ get: jest.fn(), put: jest.fn() }));

beforeEach(() => jest.clearAllMocks());

test('reads and saves only the exact employee grant with cancellation and no authentication replay', async () => {
    api.get.mockResolvedValue({ data: { read: true } });
    api.put.mockResolvedValue({ data: { saved: true } });
    const signal = new AbortController().signal;
    expect(await getProviderStatementAccess('staff-1', signal)).toEqual({ read: true });
    const payload = { mode: 'write', providers: ['tabby'], expected_policy_version: 1, expected_page_revision: 2, expected_session_scope: 'a'.repeat(64) };
    expect(await saveProviderStatementAccess('staff-1', payload, signal)).toEqual({ saved: true });
    const config = { signal, timeout: 15000, _mezanAuthRetried: true };
    expect(api.get).toHaveBeenCalledWith('/employees-v2/management/employees/staff-1/provider-statement-access', config);
    expect(api.put).toHaveBeenCalledWith('/employees-v2/management/employees/staff-1/provider-statement-access', payload, config);
});

test.each(['../owner', 'one/two', '', 'a'.repeat(129), null])('rejects invalid employee identity %j before any request', id => {
    expect(() => providerStatementAccessPath(id)).toThrow('invalid_statement_employee');
    expect(api.get).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});
