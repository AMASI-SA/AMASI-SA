import api from "../lib/api";

export async function listStoreDrivers(params = {}) {
  return (await api.get("/store-delivery/drivers", { params })).data;
}

export async function createStoreDriver(payload) {
  return (await api.post("/store-delivery/drivers", payload)).data;
}

export async function updateStoreDriver(driverId, payload) {
  return (await api.patch(`/store-delivery/drivers/${encodeURIComponent(driverId)}`, payload)).data;
}

export async function getStoreDriverEvents(driverId) {
  return (await api.get(`/store-delivery/drivers/${encodeURIComponent(driverId)}/events`)).data;
}

export async function createStoreDriverAccount(driverId, payload) {
  return (await api.post(`/store-delivery/drivers/${encodeURIComponent(driverId)}/account`, payload)).data;
}

export async function resetStoreDriverPassword(driverId, newPassword) {
  return (await api.put(`/store-delivery/drivers/${encodeURIComponent(driverId)}/account/password`, { new_password: newPassword })).data;
}

export async function disableStoreDriverAccount(driverId) {
  return (await api.delete(`/store-delivery/drivers/${encodeURIComponent(driverId)}/account`)).data;
}

export async function createDriverHandoverSession(driverId) {
  return (await api.post("/store-delivery/handover/sessions", { driver_id: driverId })).data;
}

export async function scanDriverHandoverShipment(sessionId, barcode) {
  return (await api.post(`/store-delivery/handover/sessions/${encodeURIComponent(sessionId)}/scan`, { barcode })).data;
}

export async function confirmDriverHandoverSession(sessionId) {
  return (await api.post(`/store-delivery/handover/sessions/${encodeURIComponent(sessionId)}/confirm`)).data;
}

export async function listActiveStoreDeliveryAssignments(params = {}) {
  return (await api.get("/store-delivery/assignments", { params })).data;
}

export async function reassignStoreDeliveryAssignment(assignmentId, driverId, reason = "") {
  return (await api.post(`/store-delivery/assignments/${encodeURIComponent(assignmentId)}/reassign`, {
    driver_id: driverId,
    reason,
  })).data;
}

export async function listPendingDriverPaymentReviews(params = {}) {
  return (await api.get("/store-delivery/payment-review/pending", { params })).data;
}

export async function reviewDriverPayment(assignmentId, decision, note = "") {
  return (await api.post(`/store-delivery/payment-review/${encodeURIComponent(assignmentId)}`, { decision, note })).data;
}

export async function listOfficialBusinessBankAccounts() {
  return (await api.get("/store-delivery/payment-review/bank-accounts")).data;
}
