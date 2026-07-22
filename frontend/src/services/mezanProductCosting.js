export function normalizeCustomText(value) {
    return String(value ?? "")
        .normalize("NFKC")
        .trim()
        .replace(/\s+/g, " ");
}

export function buildConfigurationKey({ sku, color, customerName }) {
    return [
        String(sku || "").trim().toUpperCase(),
        `color=${String(color || "").trim().toLowerCase()}`,
        `name=${normalizeCustomText(customerName)}`,
    ].join("|");
}

function optionRuleMatches(rule, optionKey, valueKey) {
    return rule?.when?.option_key === optionKey && rule?.when?.value_key === valueKey;
}

export function getOptionRuleSummary(recipe, optionKey, valueKey) {
    const rules = (recipe?.option_rules || []).filter((rule) => (
        optionRuleMatches(rule, optionKey, valueKey)
    ));
    const effects = rules.flatMap((rule) => rule.effects || []);
    return {
        fixed_cost_delta: effects.reduce((sum, effect) => (
            effect.type === "fixed_cost_delta" ? sum + Number(effect.amount || 0) : sum
        ), 0),
        resource_ids: [...new Set(effects
            .filter((effect) => effect.type === "add_component" && effect.resource_id)
            .map((effect) => effect.resource_id))],
    };
}

export function setOptionFixedCostDelta(recipe, optionKey, valueKey, rawAmount) {
    const amount = Math.max(0, Number(rawAmount || 0));
    const rules = recipe?.option_rules || [];
    const matching = rules.filter((rule) => optionRuleMatches(rule, optionKey, valueKey));
    const remaining = rules.filter((rule) => !optionRuleMatches(rule, optionKey, valueKey));
    const baseRule = matching[0] || {
        id: `${optionKey}-${valueKey}-rule`,
        when: { option_key: optionKey, value_key: valueKey },
        effects: [],
    };

    const nonFixedEffects = matching
        .flatMap((rule) => rule.effects || [])
        .filter((effect) => effect.type !== "fixed_cost_delta");
    const deduplicatedEffects = nonFixedEffects.filter((effect, index, all) => (
        all.findIndex((candidate) => (
            candidate.type === effect.type
            && candidate.resource_id === effect.resource_id
            && Number(candidate.quantity || 0) === Number(effect.quantity || 0)
        )) === index
    ));
    const fixedEffect = amount > 0 ? [{
        type: "fixed_cost_delta",
        amount,
        label: `فرق تكلفة داخلي لخيار ${valueKey}`,
        source: "employee_preview",
    }] : [];

    return {
        ...recipe,
        option_rules: [
            ...remaining,
            { ...baseRule, effects: [...deduplicatedEffects, ...fixedEffect] },
        ],
    };
}

function resourceMap(resources) {
    return new Map((resources || []).map((resource) => [resource.id, resource]));
}

function lineFromResource(resource, quantity, source, reason) {
    const unitCost = resource?.unit_cost;
    const known = unitCost !== null && unitCost !== undefined && unitCost !== "";
    return {
        id: `${source}:${resource?.id || "missing"}`,
        type: "resource",
        source,
        reason: reason || null,
        resource_id: resource?.id || null,
        code: resource?.code || "—",
        name: resource?.name || "مكوّن غير موجود",
        kind: resource?.kind || "unknown",
        quantity: Number(quantity || 0),
        unit_cost: known ? Number(unitCost) : null,
        total_cost: known ? Number(unitCost) * Number(quantity || 0) : null,
        cost_known: known,
    };
}

export function resolveRecipeLines({ recipe, resources, selections }) {
    const byId = resourceMap(resources);
    const lines = (recipe?.base_lines || []).map((line) => (
        lineFromResource(byId.get(line.resource_id), line.quantity, "base", line.reason)
    ));

    for (const rule of recipe?.option_rules || []) {
        const selected = selections?.[rule.when?.option_key];
        if (selected !== rule.when?.value_key) continue;

        for (const effect of rule.effects || []) {
            if (effect.type === "add_component") {
                lines.push(lineFromResource(
                    byId.get(effect.resource_id),
                    effect.quantity,
                    `option:${rule.id}`,
                    "مكوّن حسب اختيار العميل",
                ));
            }
            if (effect.type === "fixed_cost_delta") {
                const amount = Number(effect.amount || 0);
                lines.push({
                    id: `option:${rule.id}:fixed-cost`,
                    type: "fixed_cost_delta",
                    source: `option:${rule.id}`,
                    reason: effect.label || "فرق تكلفة خيار",
                    resource_id: null,
                    code: "OPTION",
                    name: effect.label || "فرق تكلفة خيار",
                    kind: "option_cost",
                    quantity: 1,
                    unit_cost: amount,
                    total_cost: amount,
                    cost_known: true,
                });
            }
        }
    }

    return lines;
}

export function calculateConfigurationCost({ recipe, resources, selections }) {
    const lines = resolveRecipeLines({ recipe, resources, selections });
    const knownTotal = lines.reduce((sum, line) => (
        line.cost_known ? sum + Number(line.total_cost || 0) : sum
    ), 0);
    const missingLines = lines.filter((line) => !line.cost_known);
    return {
        lines,
        known_total: knownTotal,
        missing_lines: missingLines,
        complete: missingLines.length === 0,
    };
}
