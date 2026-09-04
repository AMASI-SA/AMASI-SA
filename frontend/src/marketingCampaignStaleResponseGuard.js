import api from "./lib/api";

let latestSequence = 0;
let latestData = null;

function pathOnly(config = {}) {
  return String(config?.url || "").split("?", 1)[0].replace(/\/+$/, "");
}

export function isSnapchatCampaignResponse(response) {
  return pathOnly(response?.config) === "/integrations-v2/snapchat_ads/campaign-report";
}

export function chooseCampaignResponseData({
  sequence,
  data,
  state,
}) {
  const current = Number(sequence || 0);
  const nextState = {
    latestSequence: Number(state?.latestSequence || 0),
    latestData: state?.latestData ?? null,
  };
  if (current <= 0) {
    return { data, state: nextState, stale: false };
  }
  if (current >= nextState.latestSequence) {
    return {
      data,
      state: { latestSequence: current, latestData: data },
      stale: false,
    };
  }
  return {
    data,
    state: nextState,
    stale: true,
  };
}

api.interceptors.response.use((response) => {
  if (!isSnapchatCampaignResponse(response)) return response;
  const selected = chooseCampaignResponseData({
    sequence: response?.config?._mezanCampaignRequestSequence,
    data: response.data,
    state: { latestSequence, latestData },
  });
  latestSequence = selected.state.latestSequence;
  latestData = selected.state.latestData;
  if (!selected.stale) return response;
  return {
    ...response,
    config: {
      ...(response.config || {}),
      _mezanStaleCampaignResponseIgnoredByConsumer: true,
    },
  };
});

export const CAMPAIGN_STALE_RESPONSE_POLICY = Object.freeze({
  older_responses_cannot_replace_newer_range: true,
  replacement_uses_latest_successful_payload: false,
  provider_writes_allowed: false,
});
