export const feeProviders = {salla: "سلة", tamara: "تمارا", tabby: "تابي", emkan: "إمكان"};
export const feeRails = {mada: "مدى", credit_card: "بطاقة ائتمانية", debit_card: "بطاقة بنكية", visa: "فيزا",
    mastercard: "ماستركارد", apple_pay: "Apple Pay", google_pay: "Google Pay", stc_pay: "STC Pay", salla_wallet: "محفظة سلة"};

export function policyTax(policy) {
    const parts = policy.components?.length ? policy.components : [policy];
    const terms = parts.map(part => {
        if (part.vat_treatment === "exempt" || part.vat_treatment === "not_applicable") return "0";
        if (part.vat_rate == null) return "حسب الأجزاء";
        return String(Number(part.vat_rate)) + (part.vat_treatment === "inclusive" ? " (مشمولة)" : "");
    });
    return new Set(terms).size === 1 ? terms[0] : "حسب الأجزاء";
}

export function paymentFeeRows(items, policies, sourceMode, day) {
    const capture = policies.filter(p => p.status === "active" && p.currency === "SAR"
        && (p.event_type || "capture") === "capture");
    const providersWithPolicies = new Set(capture.map(p => p.provider));
    const active = capture.filter(p => p.effective_from <= day && (!p.effective_to || p.effective_to >= day));
    const rows = new Map();
    for (const method of items) {
        const scope = method.fee_scope;
        const governed = scope && scope.provider !== "paypal"
            && (sourceMode === "mz2_policies" || providersWithPolicies.has(scope.provider));
        if (!governed) {
            const key = "setting:" + (method.inherits_from || method.name).toLowerCase();
            const existing = rows.get(key);
            if (existing) {
                existing.names.push(method.name);
                if (method.editable) existing.method = method;
            } else {
                rows.set(key, {key, method, names: [method.name],
                    label: scope?.provider === "paypal" ? "باي بال" : method.inherits_from || method.name});
            }
            continue;
        }
        const key = "policy:" + scope.provider + "/" + (scope.payment_method || "");
        if (rows.has(key)) {
            rows.get(key).names.push(method.name);
            continue;
        }
        const matches = active.filter(p => p.provider === scope.provider
            && (!scope.payment_method || !p.payment_method || p.payment_method === scope.payment_method));
        const exact = scope.payment_method ? matches.filter(p => p.payment_method === scope.payment_method) : [];
        // Plan-specific and generic prices may coexist. A range of tariffs
        // should not masquerade as a single per-order fee.
        const candidates = matches.some(p => p.plan_id) ? matches : (exact.length ? exact : matches);
        const single = candidates.length === 1 && !candidates[0].plan_id
            && (!candidates[0].payment_method || candidates[0].payment_method === scope.payment_method);
        rows.set(key, {key, names: [method.name], label: feeRails[scope.payment_method] || feeProviders[scope.provider],
            scope, method, policies: candidates, policy: single ? candidates[0] : null});
    }
    return [...rows.values()];
}
