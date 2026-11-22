import api from '../lib/api';

export function providerStatementAccessPath(employeeId) {
    if (typeof employeeId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(employeeId)) {
        throw new Error('invalid_statement_employee');
    }
    return `/employees-v2/management/employees/${employeeId}/provider-statement-access`;
}

export async function getProviderStatementAccess(employeeId, signal) {
    return (await api.get(providerStatementAccessPath(employeeId), {
        signal, timeout: 15000, _mezanAuthRetried: true,
    })).data;
}

export async function saveProviderStatementAccess(employeeId, payload, signal) {
    // A grant save uses observed account/revisions. A lost response requires a
    // fresh read; neither authentication refresh nor this adapter replays it.
    return (await api.put(providerStatementAccessPath(employeeId), payload, {
        signal, timeout: 15000, _mezanAuthRetried: true,
    })).data;
}
