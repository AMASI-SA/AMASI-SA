import api from '../lib/api';

const path = employeeId => `/employees-v2/management/employees/${encodeURIComponent(employeeId)}/customer-refund-access`;

export async function getCustomerRefundAccess(employeeId, signal) {
    return (await api.get(path(employeeId), { signal, timeout: 30000 })).data;
}

export async function saveCustomerRefundAccess(employeeId, payload) {
    return (await api.put(path(employeeId), payload, { timeout: 30000 })).data;
}
