import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";
export async function readGoogleNamespacePrerequisites({ signal } = {}) {
    return (await api.get(`${GOOGLE_ADS_ROOT}/automation/namespace-prerequisites`, { signal })).data;
}
