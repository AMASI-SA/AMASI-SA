import api from "../lib/api";
import { toast } from "sonner";
import { componentNameAlreadyExists } from "../lib/componentCreationRules";

export const MEZAN_COMPONENT_PREVIEW_META = {
    mode: "production",
    label: "كتالوج مكونات وتكاليف فعلي داخل Mezan OS",
    writes_enabled: true,
    inventory_writes_enabled: false,
    source: "mezan_cost_resources_v2",
};

function normalizeSearch(value) {
    return String(value || "")
        .toLowerCase()
        .normalize("NFKC")
        .replace(/[\u064B-\u0652\u0670\u0640]/g, "")
        .replace(/[أإآا]/g, "ا")
        .replace(/ة/g, "ه")
        .replace(/ى/g, "ي")
        .trim();
}

export async function getMezanComponentWorkspace() {
    const response = await api.get("/components-v2/workspace");
    return response.data;
}

export async function saveMezanComponentPreviewCost(componentId, amount) {
    try {
        await api.put(`/components-v2/${encodeURIComponent(componentId)}/cost`, { amount });
        return { ok: true, component_workspace: await getMezanComponentWorkspace() };
    } catch (error) {
        return { ok: false, code: error?.response?.data?.detail?.code || "invalid_cost" };
    }
}

export async function addMezanComponentPreview(component) {
    try {
        const workspace = await getMezanComponentWorkspace();
        if (componentNameAlreadyExists(workspace?.components, component.name)) {
            toast.error("يوجد مكوّن أو خدمة بهذا الاسم من قبل.");
            return { ok: false, code: "component_name_exists" };
        }
        const response = await api.post("/components-v2", {
            code: component.code,
            name: component.name,
            kind: component.kind,
            unit: component.unit || (component.kind === "stock_component" ? "piece" : "job"),
            track_inventory: component.kind === "stock_component",
            requires_preparation: component.kind === "service" && component.requires_preparation === true,
            category_ids: component.category_ids || [],
            unit_cost: component.unit_cost,
            description: component.description || "",
        });
        return {
            ok: true,
            resource: response.data.resource,
            component_workspace: await getMezanComponentWorkspace(),
        };
    } catch (error) {
        return { ok: false, code: error?.response?.data?.detail?.code || "invalid_component" };
    }
}

export async function updateMezanComponent(componentId, component) {
    try {
        const workspace = await getMezanComponentWorkspace();
        if (componentNameAlreadyExists(workspace?.components, component.name, componentId)) {
            toast.error("يوجد مكوّن أو خدمة بهذا الاسم من قبل.");
            return { ok: false, code: "component_name_exists" };
        }
        const response = await api.put(`/components-v2/${encodeURIComponent(componentId)}`, {
            code: component.code,
            name: component.name,
            kind: component.kind,
            unit: component.unit || (component.kind === "stock_component" ? "piece" : "job"),
            requires_preparation: component.kind === "service" && component.requires_preparation === true,
            category_ids: component.category_ids || [],
            unit_cost: component.unit_cost,
            description: component.description || "",
        });
        return {
            ok: true,
            resource: response.data.resource,
            impacted_bindings: response.data.impacted_bindings || 0,
            component_workspace: await getMezanComponentWorkspace(),
        };
    } catch (error) {
        return { ok: false, code: error?.response?.data?.detail?.code || "invalid_component" };
    }
}

export async function setMezanComponentStatus(componentId, status, reason = "") {
    try {
        const response = await api.put(
            `/components-v2/${encodeURIComponent(componentId)}/status`,
            { status, reason },
        );
        return {
            ok: true,
            resource: response.data.resource,
            status: response.data.status,
            changed: response.data.changed,
            impacted_bindings: response.data.impacted_bindings || 0,
        };
    } catch (error) {
        return {
            ok: false,
            code: error?.response?.data?.detail?.code || "invalid_component_status",
        };
    }
}

export async function linkMezanComponentToProductPreview({ productId, resourceId, quantity, condition }) {
    try {
        if (!condition?.option_key || !condition?.value_key) {
            return { ok: false, code: "option_value_not_found" };
        }
        await api.put(
            `/products-v2/${encodeURIComponent(productId)}/option-costs/${encodeURIComponent(condition.option_key)}/${encodeURIComponent(condition.value_key)}`,
            { mode: "resource", resource_id: resourceId, quantity },
        );
        return { ok: true, component_workspace: await getMezanComponentWorkspace() };
    } catch (error) {
        return { ok: false, code: error?.response?.data?.detail?.code || "link_exists" };
    }
}

export async function unlinkMezanComponentFromProductPreview({ productId, condition }) {
    try {
        if (!condition?.option_key || !condition?.value_key) {
            return { ok: false, code: "option_value_not_found" };
        }
        await api.delete(
            `/products-v2/${encodeURIComponent(productId)}/option-costs/${encodeURIComponent(condition.option_key)}/${encodeURIComponent(condition.value_key)}`,
        );
        return { ok: true, component_workspace: await getMezanComponentWorkspace() };
    } catch (error) {
        return { ok: false, code: error?.response?.data?.detail?.code || "link_not_found" };
    }
}

export function summarizeMezanComponents(components) {
    const rows = components || [];
    return {
        total: rows.length,
        active: rows.filter((row) => row.status !== "inactive").length,
        inactive: rows.filter((row) => row.status === "inactive").length,
        stock_components: rows.filter((row) => row.track_inventory).length,
        labor_services: rows.filter((row) => !row.track_inventory).length,
        missing_cost: rows.filter((row) => row.reference_cost?.amount == null).length,
    };
}

export function filterMezanComponents(components, { query = "", filter = "all", status = "active" } = {}) {
    const needle = normalizeSearch(query);
    return (components || []).filter((component) => {
        const matchesFilter = (
            filter === "all"
            || (filter === "stock" && component.track_inventory)
            || (filter === "service" && !component.track_inventory)
            || (filter === "missing_cost" && component.reference_cost?.amount == null)
        );
        if (!matchesFilter) return false;
        if (status === "active" && component.status === "inactive") return false;
        if (status === "inactive" && component.status !== "inactive") return false;
        if (!needle) return true;
        return normalizeSearch([
            component.name,
            component.code,
            component.category,
            component.description,
        ].join(" ")).includes(needle);
    });
}
