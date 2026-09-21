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
      return_case_not_inspected: "يجب حفظ فحص المرتجع قبل إعادته للمخزون.",
      return_inventory_not_ready_for_restock: "المرتجع غير جاهز لحركة المخزون.",
      return_restock_item_not_found: "قطعة المرتجع غير موجودة ضمن الفحص.",
      return_restock_source_not_found: "مصدر الـlot الأصلي غير موجود.",
      return_restock_exceeds_sellable_quantity: "كمية Restock تتجاوز الكمية الصالحة المعتمدة.",
      return_restock_exceeds_source_quantity: "كمية Restock تتجاوز الكمية التي خرجت من هذا الـlot.",
      return_restock_location_not_compatible: "الخانة لا تقبل نفس المنتج/التجهيز أو لا توجد بها سعة كافية.",
      return_restock_location_barcode_mismatch: "باركود الخانة لا يطابق الخانة المختارة.",
      return_restock_request_conflict: "معرف Restock استُخدم سابقًا ببيانات مختلفة.",
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

export async function getReturnRestockOptions(caseId) {
  const id = requiredId(caseId, "رقم حالة المرتجع");
  try {
    const { data } = await api.get(
      `/returns-v2/cases/${encodeURIComponent(id)}/restock-options`,
    );
    return data;
  } catch (error) {
    throw new Error(errorMessage(error, "تعذّر تحميل خيارات إعادة المخزون."));
  }
}

export async function restockReturnInventory(caseId, payload) {
  const id = requiredId(caseId, "رقم حالة المرتجع");
  try {
    const { data } = await api.post(
      `/returns-v2/cases/${encodeURIComponent(id)}/restock`,
      payload,
    );
    return data;
  } catch (error) {
    throw new Error(errorMessage(error, "تعذّر إعادة القطعة إلى المخزون."));
  }
}
