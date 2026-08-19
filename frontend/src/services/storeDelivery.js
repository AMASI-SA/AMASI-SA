import api from "./api";

export async function listStoreDrivers(params = {}) {
  const response = await api.get("/store-delivery/drivers", { params });
  return response.data;
}

export async function createStoreDriver(payload) {
  const response = await api.post("/store-delivery/drivers", payload);
  return response.data;
}

export async function updateStoreDriver(driverId, payload) {
  const response = await api.patch(`/store-delivery/drivers/${driverId}`, payload);
  return response.data;
}

export async function getStoreDriverEvents(driverId) {
  const response = await api.get(`/store-delivery/drivers/${driverId}/events`);
  return response.data;
}

export async function createDriverHandoverSession(driverId) {
  const response = await api.post("/store-delivery/handover/sessions", { driver_id: driverId });
  return response.data;
}

export async function scanDriverHandoverShipment(sessionId, barcode) {
  const response = await api.post(`/store-delivery/handover/sessions/${sessionId}/scan`, { barcode });
  return response.data;
}

export async function confirmDriverHandoverSession(sessionId) {
  const response = await api.post(`/store-delivery/handover/sessions/${sessionId}/confirm`);
  return response.data;
}
