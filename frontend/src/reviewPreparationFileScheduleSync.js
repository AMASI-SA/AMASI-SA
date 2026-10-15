import { toast } from "sonner";

import { updatePreparationFileSchedule } from "./services/preparationWorkService";

const ROOT_ID = "mezan-preparation-file-schedule-sync-root";

export function preparationFileCreatedSchedule(detail, pending) {
  const fileNumber = String(detail?.file_number || "").trim();
  const mode = pending?.mode === "required" ? "required" : "automatic";
  const requiredDueAt = mode === "required"
    ? String(pending?.requiredDueAt || "").trim()
    : null;
  if (!fileNumber) return null;
  if (mode === "required" && !requiredDueAt) return null;
  return { fileNumber, mode, requiredDueAt };
}

async function persistSchedule(event) {
  const pending = window.__mezanPreparationFileSchedulePending;
  const payload = preparationFileCreatedSchedule(event?.detail, pending);
  if (!payload) {
    delete window.__mezanPreparationFileSchedulePending;
    return;
  }
  try {
    await updatePreparationFileSchedule(payload.fileNumber, {
      mode: payload.mode,
      requiredDueAt: payload.requiredDueAt,
    });
    toast.success(
      payload.mode === "required"
        ? "تم حفظ الموعد الإجباري لملف التجهيز."
        : "تم اعتماد الوقت التلقائي لملف التجهيز.",
    );
  } catch (error) {
    toast.error(error.message || "تم إنشاء الملف، لكن تعذّر حفظ موعد التجهيز.");
  } finally {
    delete window.__mezanPreparationFileSchedulePending;
  }
}

function start() {
  if (typeof document === "undefined" || !document.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);
  window.addEventListener("mezan:preparation-file-created", persistSchedule);
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
