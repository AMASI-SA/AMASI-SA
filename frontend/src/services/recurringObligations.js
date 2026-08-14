import api from "../lib/api";


export async function loadRecurringObligationsWorkspace() {
    const [obligations, options] = await Promise.all([
        api.get("/recurring-obligations"),
        api.get("/recurring-obligations/options"),
    ]);
    return {
        items: obligations.data?.items || [],
        summary: obligations.data?.summary || {},
        options: options.data || { locations: [], employees: [] },
        sourceContract: obligations.data?.source_contract || "",
    };
}

export async function createRecurringObligation(payload) {
    const response = await api.post("/recurring-obligations", payload);
    return response.data;
}

export async function updateRecurringObligation(id, payload) {
    const response = await api.put(`/recurring-obligations/${id}`, payload);
    return response.data;
}

export async function loadRecurringInvoices(id) {
    const response = await api.get(`/recurring-obligations/${id}/invoices`);
    return response.data?.items || [];
}

export async function createRecurringInvoice(id, payload) {
    const response = await api.post(`/recurring-obligations/${id}/invoices`, payload);
    return response.data;
}
