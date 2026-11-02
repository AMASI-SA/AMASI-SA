import api from "../lib/api";

const SETUP = "/accounting-module/onboarding";
const SHIPPING = "/accounting-module/shipping-v2";
const SETTLEMENTS = "/financial-provider-apps/accounting-module/settlements";
const entry = (key, label, path, valid, params) => ({ key, label, path, valid, params });
const items = value => Array.isArray(value?.items);
const identity = value => typeof value === "string" && value.trim() === value && value.length > 0 && value.length <= 160;

// Existing GET contracts only. Operational catalogues are context, never opening balances.
export async function getOnboardingDomainContext(stage, { driverId, courierId } = {}) {
    let sources = [];
    if (driverId !== undefined || courierId !== undefined) {
        const driver = stage === "drivers" && identity(driverId) && courierId === undefined;
        const courier = ["courier_contracts", "courier_balances"].includes(stage) && identity(courierId) && driverId === undefined;
        if (!driver && !courier) throw new Error("onboarding_domain_identity_required");
        const kind = driver ? "store_driver" : "courier", id = driver ? driverId : courierId;
        sources.push(entry("statement", "كشف ذمة MZ2", `${SHIPPING}/statements/${kind}/${encodeURIComponent(id)}`,
            value => value?.ledger_source === "accounting_v2" && value.party_type === kind && value.party_id === id && Array.isArray(value.entries)));
        if (driver) sources.push(
            entry("cash", "أدلة النقد الفعلي والمطابقة", `${SHIPPING}/driver-cash/${encodeURIComponent(id)}`,
                value => value?.driver_id === id && value.read_only === true && value.scope === "captured_delivered_cash_only" && items(value)),
            entry("history", "مراجعات التحويل والشبكة", `${SHIPPING}/driver-payment-history`,
                value => value?.ledger_source === "accounting_v2" && value.read_only === true && items(value)
                    && value.items.every(row => row.driver_id === id), { driver_id: id, limit: 50 }),
        );
    } else if (["courier_contracts", "courier_balances"].includes(stage)) {
        sources = [
            entry("couriers", "شركات الشحن التشغيلية وربط البنك", `${SETTLEMENTS}/courier-bindings`, items),
            entry("native", "هويات وعقود الشحن في MZ2", `${SHIPPING}/rich-contracts`, value => Array.isArray(value?.couriers) && Array.isArray(value?.contracts)),
        ];
    } else if (stage === "drivers") {
        sources = [entry("drivers", "المندوبون التشغيليون", "/store-delivery/drivers", items),
            entry("shipping", "جاهزية هويات المندوبين في MZ2", `${SHIPPING}/context`, value => Array.isArray(value?.store_drivers) && value.stages !== undefined)];
    } else if (stage === "payment_fees") {
        sources = [entry("fees", "سياسات الرسوم المحفوظة", `${SETUP}/fee-policies`, items),
            entry("bindings", "ربط مزودي الدفع بالبنك", `${SETTLEMENTS}/context`, value => Array.isArray(value?.bindings))];
    } else if (["prepaid", "obligations"].includes(stage)) {
        sources = [entry("recurring", "الالتزامات التشغيلية القائمة", "/recurring-obligations", value => items(value) && value.source_contract === "operating_recurring_obligations_v2"),
            entry("facts", "أرصدة موثقة في MZ2", `${SETUP}/typed-facts`, items)];
    }
    const results = await Promise.allSettled(sources.map(async source => {
        const result = source.params ? await api.get(source.path, { params: source.params }) : await api.get(source.path);
        if (!source.valid(result.data)) throw new Error("onboarding_domain_response_invalid");
        return result.data;
    }));
    return { sources: sources.map((source, index) => {
        const result = results[index];
        return result.status === "fulfilled"
            ? { key: source.key, label: source.label, status: "ready", data: result.value }
            : { key: source.key, label: source.label, status: "error", httpStatus: result.reason?.response?.status,
                invalid: result.reason?.message === "onboarding_domain_response_invalid" };
    }) };
}
