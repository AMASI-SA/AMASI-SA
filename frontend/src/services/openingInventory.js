import api from "../lib/api";

export async function getOpeningInventoryContext() {
    return (await api.get("/opening-inventory/context")).data;
}

export async function importOpeningInventory(document) {
    const form = new FormData();
    form.append("file", new Blob([JSON.stringify(document)], { type: "application/json" }), "opening-inventory.json");
    return (await api.post("/opening-inventory/imports", form)).data;
}

export async function getOpeningInventoryImport(id) {
    return (await api.get(`/opening-inventory/imports/${encodeURIComponent(id)}`)).data;
}

export async function approveOpeningInventory(row) {
    return (await api.post(`/opening-inventory/imports/${encodeURIComponent(row.id)}/approve`, {
        approve: true, evidence_sha256: row.evidence_sha256, baseline_sha256: row.baseline_sha256,
    })).data;
}
