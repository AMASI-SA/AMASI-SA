import api from "../lib/api";
import { toast } from "sonner";

const ERROR_LABELS = {
  component_category_name_required: "أدخل اسم التصنيف.",
  component_category_required: "يجب أن يتبع المكوّن أو الخدمة تصنيفًا واحدًا على الأقل.",
  component_category_exists: "يوجد تصنيف بالاسم نفسه.",
  component_category_not_found: "التصنيف المحدد غير موجود.",
  component_category_used_by_group: "لا يمكن إزالة التصنيف لأن العنصر مستخدم داخل قروب تابع له.",
  component_group_requires_two_members: "اختر عنصرين مختلفين على الأقل لإنشاء القروب.",
  invalid_component_group_kind: "نوع القروب غير صحيح.",
  component_group_resource_missing: "أحد عناصر القروب لم يعد موجودًا.",
  component_group_category_mismatch: "أحد العناصر لا يتبع التصنيف المحدد.",
  component_group_kind_mismatch: "لا يمكن خلط الخدمات والمكونات داخل القروب نفسه.",
  component_group_exists: "يوجد مجموعة لهذا التصنيف بنفس المكونات أو الخدمات.",
  component_group_not_found: "القروب غير موجود.",
};

function organizationError(error, fallback) {
  const detail = error?.response?.data?.detail;
  const code = detail?.code || "";
  const result = new Error(detail?.message || ERROR_LABELS[code] || code || error?.message || fallback);
  result.code = code;
  result.detail = detail;
  return result;
}

export async function createComponentCategory(name) {
  try {
    return (await api.post("/components-v2/categories", { name })).data;
  } catch (error) {
    throw organizationError(error, "تعذر إنشاء التصنيف.");
  }
}

export async function updateComponentCategory(categoryId, name) {
  try {
    return (await api.put(
      `/components-v2/categories/${encodeURIComponent(categoryId)}`,
      { name },
    )).data;
  } catch (error) {
    throw organizationError(error, "تعذر تعديل التصنيف.");
  }
}

export async function saveResourceCategories(resourceId, categoryIds) {
  try {
    return (await api.put(
      `/components-v2/${encodeURIComponent(resourceId)}/categories`,
      { category_ids: categoryIds },
    )).data;
  } catch (error) {
    throw organizationError(error, "تعذر حفظ تصنيفات العنصر.");
  }
}

export async function saveComponentGroup(group) {
  const payload = {
    category_id: group.category_id,
    group_kind: group.group_kind,
    resource_ids: group.resource_ids,
  };
  try {
    if (group.id) {
      return (await api.put(
        `/components-v2/groups/${encodeURIComponent(group.id)}`,
        payload,
      )).data;
    }
    return (await api.post("/components-v2/groups", payload)).data;
  } catch (error) {
    const result = organizationError(error, "تعذر حفظ القروب.");
    if (result.code === "component_group_exists") toast.error(result.message);
    throw result;
  }
}

export function generatedComponentGroupName(resources, resourceIds) {
  const byId = new Map((resources || []).map((row) => [String(row.id), row]));
  return (resourceIds || [])
    .map((resourceId) => byId.get(String(resourceId))?.name || String(resourceId))
    .filter(Boolean)
    .join(" - ");
}

export function resourcesForComponentCategory(resources, categoryId, groupKind = "all") {
  return (resources || []).filter((resource) => {
    const categories = (resource.category_ids || []).map(String);
    const categoryMatches = !categoryId || categories.includes(String(categoryId));
    const kind = resource.track_inventory ? "component" : "service";
    return categoryMatches && (groupKind === "all" || groupKind === kind);
  });
}

export function activeResourcesForComponentCategory(resources, categoryId, groupKind = "all") {
  return resourcesForComponentCategory(resources, categoryId, groupKind)
    .filter((resource) => resource.status !== "inactive");
}
