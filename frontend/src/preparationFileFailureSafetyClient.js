import api from "./lib/api";

const OLD_DRAFT_PATH = "/preparation-file-registry-v1/drafts";
const SAFE_DRAFT_PATH = "/preparation-file-safety-v1/drafts";
const CATALOG_PATH = "/reviewed-products-v1/catalog";
const FINALIZE_PREFIX = "/preparation-file-registry-v1/finalize/";
const SAFETY_SKIP = "_mezanPreparationSafetySkip";

function text(value) {
  return String(value || "").trim();
}

function pathname(value) {
  const raw = text(value);
  if (!raw) return "";
  try {
    return new URL(raw, "https://mezan.local").pathname.replace(/^\/api/, "");
  } catch {
    return raw.split("?")[0];
  }
}

function objectPayload(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return { ...value };
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed;
      }
    } catch {
      return {};
    }
  }
  return {};
}

function currentMetadata() {
  if (typeof window === "undefined") return {};
  const metadata = window.__mezanPreparationFileMetadata || {};
  return {
    schedule_mode: metadata.scheduleMode === "required"
      ? "required"
      : "automatic",
    required_due_at: metadata.scheduleMode === "required"
      ? metadata.requiredDueAt || null
      : null,
  };
}

export function safeDraftConfig(config = {}) {
  if (config[SAFETY_SKIP] || pathname(config.url) !== OLD_DRAFT_PATH) {
    return config;
  }
  return {
    ...config,
    url: SAFE_DRAFT_PATH,
    data: {
      ...objectPayload(config.data),
      ...currentMetadata(),
    },
  };
}

export function finalizeClientRequestId(config = {}) {
  const path = pathname(config.url);
  if (!path.startsWith(FINALIZE_PREFIX)) return "";
  return decodeURIComponent(path.slice(FINALIZE_PREFIX.length));
}

async function recoverStaleBeforeCatalog(response) {
  const config = response?.config || {};
  if (
    config[SAFETY_SKIP]
    || pathname(config.url) !== CATALOG_PATH
    || String(config.method || "get").toLowerCase() !== "get"
  ) {
    return response;
  }
  try {
    const recovery = await api.post(
      "/preparation-file-safety-v1/recover-stale",
      null,
      { [SAFETY_SKIP]: true },
    );
    if (Number(recovery?.data?.recovered_count || 0) <= 0) return response;
    return await api.get(CATALOG_PATH, {
      params: config.params,
      [SAFETY_SKIP]: true,
    });
  } catch {
    return response;
  }
}

async function releaseAfterFinalizeFailure(error) {
  const config = error?.config || {};
  if (config[SAFETY_SKIP]) return Promise.reject(error);
  const clientRequestId = finalizeClientRequestId(config);
  if (!clientRequestId) return Promise.reject(error);

  try {
    const recovery = await api.post(
      `/preparation-file-safety-v1/requests/${encodeURIComponent(clientRequestId)}/release`,
      null,
      { [SAFETY_SKIP]: true },
    );
    if (recovery?.data?.released) {
      const message = (
        "تعذّر إكمال ملف التجهيز، وتمت إعادة القطع تلقائيًا إلى تمت المراجعة. "
        + "أكمل بيانات الملف ثم أعد المحاولة."
      );
      error.message = message;
      error.response = error.response || {};
      error.response.data = {
        ...(error.response.data || {}),
        detail: {
          code: "preparation_file_failed_and_units_released",
          message,
        },
      };
    }
  } catch {
    // Keep the original failure. Stale recovery runs again when the reviewed
    // catalogue opens, so an interrupted recovery cannot strand the units.
  }
  return Promise.reject(error);
}

api.interceptors.request.use(safeDraftConfig);
api.interceptors.response.use(
  recoverStaleBeforeCatalog,
  releaseAfterFinalizeFailure,
);

export { SAFETY_SKIP };
