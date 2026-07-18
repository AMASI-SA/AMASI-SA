import api from "../lib/api";

function errorMessage(error, fallback) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string" && detail.message.trim())
      return detail.message;
    const messages = {
      owner_only: "هذه الصفحة متاحة للمالك فقط.",
      order_not_found: "لم يتم العثور على الطلب.",
      return_case_not_found: "لم يتم العثور على حالة المرتجع.",
      return_case_not_draft: "تم اعتماد هذه الحالة مسبقًا.",
      version_conflict:
        "تم تعديل الحالة من جلسة أخرى. حدّث الصفحة ثم أعد المحاولة.",
      decision_option_not_available: "الخيار المحدد غير متاح لهذا التقرير.",
      duplicate_return_item: "لا يمكن تكرار نفس قطعة الطلب في المرتجع.",
      return_item_not_in_order: "إحدى القطع المختارة لا تنتمي إلى هذا الطلب.",
      return_quantity_exceeds_ordered: "كمية الإرجاع تتجاوز الكمية الأصلية.",
      inspection_items_mismatch:
        "يجب فحص كل القطع المختارة، ولو كانت الكمية المستلمة صفرًا.",
      inspection_item_not_selected: "لا يمكن فحص قطعة لم تُعتمد ضمن المرتجع.",
      received_quantity_exceeds_selected_quantity:
        "الكمية المستلمة تتجاوز الكمية المعتمدة للإرجاع.",
    };
    if (messages[detail.code]) return messages[detail.code];
  }
  return error?.message || fallback;
}

function requiredId(value, label) {
  const normalized = String(value || "").trim();
  if (!normalized) throw new Error(`${label} مطلوب.`);
  return normalized;
}

export async function getReturnWorkspace(orderNumber) {
  const number = requiredId(orderNumber, "رقم الطلب");
  try {
    const { data } = await api.get(
      `/returns-v2/orders/${encodeURIComponent(number)}`,
    );
    return {
      orderNumber: data?.order_number || number,
      shipments: Array.isArray(data?.detected_return_shipments)
        ? data.detected_return_shipments
        : [],
      cases: Array.isArray(data?.cases) ? data.cases : [],
      sourceRules: data?.source_rules || {},
    };
  } catch (error) {
    throw new Error(errorMessage(error, "تعذّر تحميل مساحة المرتجع."));
  }
}

export async function createReturnCase(orderNumber, payload) {
  const number = requiredId(orderNumber, "رقم الطلب");
  try {
    const { data } = await api.post(
      `/returns-v2/orders/${encodeURIComponent(number)}/cases`,
      payload,
    );
    return data;
  } catch (error) {
    throw new Error(errorMessage(error, "تعذّر إنشاء تقرير المرتجع."));
  }
}

export async function approveReturnCase(caseId, payload) {
  const id = requiredId(caseId, "رقم حالة المرتجع");
  try {
    const { data } = await api.post(
      `/returns-v2/cases/${encodeURIComponent(id)}/approve`,
      payload,
    );
    return data;
  } catch (error) {
    throw new Error(errorMessage(error, "تعذّر اعتماد قرار المرتجع."));
  }
}

export async function inspectReturnCase(caseId, payload) {
  const id = requiredId(caseId, "رقم حالة المرتجع");
  try {
    const { data } = await api.post(
      `/returns-v2/cases/${encodeURIComponent(id)}/inspect`,
      payload,
    );
    return data;
  } catch (error) {
    throw new Error(errorMessage(error, "تعذّر حفظ فحص المرتجع."));
  }
}
