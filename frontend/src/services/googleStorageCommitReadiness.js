import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";
const endpoint = `${GOOGLE_ADS_ROOT}/automation/storage-commit-readiness`;
export async function verifyGoogleStorageCommit({ signal } = {}) {
    return (await api.post(endpoint, {}, { signal })).data;
}
export async function readGoogleStorageCommitReceipt({ signal } = {}) {
    return (await api.get(endpoint, { signal })).data;
}
