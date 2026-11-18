import {
    DASHBOARD_ADS_PROVIDERS,
    dashboardAvailableAmount,
    dashboardCanonicalSpendMatches,
    dashboardKnownIncompleteAmount,
    dashboardSpendPeriodMatches,
} from "../services/dashboardAdsSpend";

const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function amount(value) {
    if (!(typeof value === "number" || typeof value === "string" && value.trim() !== "")) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 && parsed <= Number.MAX_SAFE_INTEGER ? parsed : null;
}

function count(value) {
    const parsed = amount(value);
    return Number.isSafeInteger(parsed) ? parsed : null;
}

function accountId(row) {
    const id = record(row) ? row.account_id : null;
    return typeof id === "string" && id.trim() && id.length <= 512 ? id : null;
}

function emptyStats(partial = false) {
    return { orders: null, sales: null, cpa: null, knownOrders: null, knownSales: null, partial };
}

function displayedAccounts(rows) {
    const result = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
        const id = accountId(row);
        if (id !== null && !result.has(id)) result.set(id, row);
    });
    return result;
}

// Conflicting duplicate evidence never chooses an arbitrary account total.
function accountEvidence(rows, providerRequired = false) {
    const result = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
        const id = accountId(row);
        if (id === null || providerRequired && row.provider !== "snapchat") return;
        result.set(id, result.has(id) ? null : row);
    });
    return result;
}

function accountSpend(row, ready) {
    const quality = row?.quality;
    return ready && row?.projection_timezone === "Asia/Riyadh"
        && quality?.amount_available === true && quality.connected !== false
        && ["complete", "provisional"].includes(quality.status)
        && quality.data_state !== "unknown_incomplete" ? amount(row.total_sar) : null;
}

function providerSpend(snapshot, provider, ready) {
    const quality = snapshot?.providers?.[provider];
    return ready && quality?.amount_available !== false
        && quality?.data_state !== "unknown_incomplete" && quality?.status !== "incomplete"
        && quality?.data_quality !== "incomplete" ? amount(snapshot?.provider_totals_sar?.[provider]) : null;
}

function metrics(row, spend, basisMatches, { account = false, coverageComplete = true } = {}) {
    if (!record(row)) return emptyStats(true);
    const attributed = account ? coverageComplete && row.attribution_complete === true
        : row.attribution_complete !== false;
    const converted = account ? row.sales_currency_conversion_complete === true
        : row.sales_currency_conversion_complete !== false;
    const orders = attributed ? count(row.salla_orders) : null;
    const sales = attributed && converted ? amount(row.salla_sales_sar) : null;
    return {
        orders,
        sales,
        cpa: basisMatches && spend !== null && orders !== null && orders > 0 ? amount(spend / orders) : null,
        knownOrders: orders ?? count(row.known_salla_orders),
        knownSales: sales ?? amount(row.known_salla_sales_sar),
        partial: orders === null || sales === null,
    };
}

function sum(values, { complete = false, integer = false } = {}) {
    if (!values.length || complete && values.some((value) => value === null)) return null;
    const known = values.filter((value) => value !== null);
    if (!known.length) return null;
    const total = known.reduce((value, next) => value + next, 0);
    return integer ? count(total) : amount(Number(total.toFixed(2)));
}

function totalMetrics(rows, spends, basisMatches) {
    const orders = sum(rows.map((row) => row.orders), { complete: true, integer: true });
    const sales = sum(rows.map((row) => row.sales), { complete: true });
    const spend = sum(spends, { complete: true });
    const partial = orders === null || sales === null || rows.some((row) => row.partial);
    return {
        orders,
        sales,
        cpa: basisMatches && !partial && spend !== null && orders > 0 ? amount(spend / orders) : null,
        knownOrders: sum(rows.map((row) => row.knownOrders), { integer: true }),
        knownSales: sum(rows.map((row) => row.knownSales)),
        partial,
    };
}

const SEGMENT_IDS = ["morning", "afternoon"];
const HOUR_MS = 60 * 60 * 1000;
const HALF_HALALA = 0.005;
const ROUNDING_EPSILON = 1e-8;
const currencyCode = (value) => typeof value === "string" && /^[A-Z]{3}$/.test(value) ? value : null;
const reasonCodes = (...lists) => [...new Set(lists.flatMap((list) => Array.isArray(list)
    ? list.filter((value) => typeof value === "string" && value.length <= 120) : []))];

function signedMoney(value) {
    return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER
        ? Number(value.toFixed(2)) : null;
}

function emptyProfit(currency = "SAR", reason = "costs_unavailable", adSpendApplicable = true) {
    return {
        adSpendApplicable,
        spendSar: null, knownSpendSar: null, nativeSpend: null, currency,
        pieces: null, knownPieces: null, productCost: null, knownProductCost: null,
        shippingCost: null, knownShippingCost: null, paymentFees: null, knownPaymentFees: null,
        bankFees: null, knownBankFees: null,
        totalCost: null, knownTotalCost: null, netProfit: null, roas: null,
        complete: false, provisional: false, reasonCodes: [reason],
    };
}

function directEvidence(value) {
    return record(value) && value.provider === "direct" && value.ad_spend_applicable === false ? value : null;
}

function unallocatedSpend(provisional = false) {
    return { sar: null, knownSar: null, native: null, currency: "SAR", provisional, reasonCodes: [] };
}

function dailySpend(snapshot, provider, account, ready) {
    const sar = account ? accountSpend(account, ready) : providerSpend(snapshot, provider, ready);
    const quality = account?.quality || snapshot?.providers?.[provider] || {};
    const currency = currencyCode(account?.currency) || "SAR";
    return {
        sar,
        knownSar: sar ?? (ready && quality.amount_available === false ? amount(quality.known_total_sar) : null),
        native: sar === null ? null : account ? currencyCode(account.currency) ? amount(account.total_native) : null : sar,
        currency,
        provisional: quality.provisional === true || quality.status === "provisional"
            || snapshot?.spend_quality?.provisional === true || snapshot?.spend_quality?.status === "provisional",
        reasonCodes: sar === null ? ["spend_unavailable"] : [],
    };
}

// This is the scope of the executive's advertising-bank-fee authority.
// Google fees are not part of that authority; customer gateway fees stay separate.
const AD_BANK_PROVIDERS = { snapchat: "snapchat_ads", meta: "meta_ads", tiktok: "tiktok_ads" };

function bankFeeRows(context, provider) {
    if (context.bankFeeRows.has(provider)) return context.bankFeeRows.get(provider);
    const bank = context.executiveSnapshot?.ads_v2?.bank_commissions;
    const expectedSpend = providerSpend(context.spendSnapshot, provider, context.spendReady);
    const nested = bank?.coverage?.[provider === "snapchat" ? "snapchat" : "meta_tiktok"];
    let result = null;
    if (context.basisMatches && record(bank) && bank.coverage?.complete === true
        && Array.isArray(bank.accounts) && bank.accounts.every(record) && expectedSpend !== null) {
        const rows = bank.accounts.filter((row) => row.provider === AD_BANK_PROVIDERS[provider]);
        const identities = new Set();
        const valid = rows.every((row) => {
            const identity = row.external_account_id || row.mezan_integration_account_id;
            const spend = amount(row.spend_sar);
            const fee = amount(row.bank_commission_fee_sar);
            const pct = amount(row.bank_commission_pct);
            if (typeof identity !== "string" || !identity.trim() || identities.has(identity)
                || spend === null || fee === null || pct === null || pct > 20
                || typeof row.apply_bank_commission !== "boolean"
                || row.apply_bank_commission === false && fee !== 0
                || row.apply_bank_commission === true && (fee > 0 && (spend === 0 || pct === 0)
                    || Math.abs(fee - spend * pct / 100) > HALF_HALALA + ROUNDING_EPSILON)) return false;
            identities.add(identity);
            return true;
        });
        const observedSpend = rows.length ? sum(rows.map((row) => amount(row.spend_sar)), { complete: true }) : 0;
        const roundingBound = rows.length * HALF_HALALA + ROUNDING_EPSILON;
        if (valid && (nested?.complete === true || !rows.length && expectedSpend === 0)
            && observedSpend !== null && Math.abs(observedSpend - expectedSpend) <= roundingBound) result = rows;
    }
    context.bankFeeRows.set(provider, result);
    return result;
}

function dailyBankFees(context, provider, account = null) {
    if (!Object.prototype.hasOwnProperty.call(AD_BANK_PROVIDERS, provider)) return 0;
    const rows = bankFeeRows(context, provider);
    if (rows === null) return null;
    const selected = account ? rows.filter((row) => row.external_account_id === account.account_id) : rows;
    if (!selected.length) return account ? accountSpend(account, context.spendReady) === 0 ? 0 : null : 0;
    if (account && (selected.length !== 1 || amount(selected[0].spend_sar) !== accountSpend(account, context.spendReady))) return null;
    return sum(selected.map((row) => amount(row.bank_commission_fee_sar)), { complete: true });
}

function totalBankFees(context) {
    const bank = context.executiveSnapshot?.ads_v2?.bank_commissions;
    const groups = Object.keys(AD_BANK_PROVIDERS).map((provider) => bankFeeRows(context, provider));
    if (groups.some((rows) => rows === null) || !record(bank)) return null;
    const rows = groups.flat();
    if (rows.length !== bank.accounts.length) return null;
    const fee = rows.length ? sum(rows.map((row) => amount(row.bank_commission_fee_sar)), { complete: true }) : 0;
    const spend = rows.length ? sum(rows.map((row) => amount(row.spend_sar)), { complete: true }) : 0;
    const subjectRows = rows.filter((row) => row.apply_bank_commission);
    const subjectSpend = subjectRows.length ? sum(subjectRows.map((row) => amount(row.spend_sar)), { complete: true }) : 0;
    if (fee === null || amount(bank.total_fee_sar) !== fee || spend === null
        || subjectSpend === null || amount(bank.fee_subject_spend_sar) === null
        || Math.abs(amount(bank.fee_subject_spend_sar) - subjectSpend) > subjectRows.length * HALF_HALALA + ROUNDING_EPSILON
        || amount(bank.total_effective_spend_sar) === null
        || Math.abs(amount(bank.total_effective_spend_sar) - spend) > rows.length * HALF_HALALA + ROUNDING_EPSILON) return null;
    const totals = context.executiveSnapshot?.totals;
    if (Object.prototype.hasOwnProperty.call(totals || {}, "ad_bank_commission_fees")
        && amount(totals.ad_bank_commission_fees) !== fee) return null;
    return fee;
}

function withBankFees(profit, fee, knownFee = fee) {
    const totalCost = profit.totalCost !== null && fee !== null ? sum([profit.totalCost, fee], { complete: true }) : null;
    const netProfit = profit.netProfit !== null && fee !== null ? signedMoney(profit.netProfit - fee) : null;
    return { ...profit, bankFees: fee, knownBankFees: knownFee, totalCost,
        knownTotalCost: sum([profit.knownTotalCost, knownFee]),
        netProfit, complete: profit.complete && fee !== null && totalCost !== null && netProfit !== null,
        reasonCodes: reasonCodes(profit.reasonCodes, fee === null ? ["ad_bank_fees_unverified"] : []),
    };
}

function profitMetrics(stats, raw, spend, basisMatches, {
    attributed = true, timed = true, compatible = true, adSpendApplicable = true,
} = {}) {
    const summary = record(raw) && raw.basis === "executive_order_costs_v1" && raw.currency === "SAR" ? raw : null;
    const permitted = attributed && timed && compatible;
    const canonical = (key, flag, integer = false) => permitted && summary?.[flag] === true
        ? (integer ? count(summary[key]) : amount(summary[key])) : null;
    const pieces = canonical("pieces", "pieces_complete", true);
    const productCost = canonical("product_cost_sar", "product_cost_complete");
    const shippingCost = canonical("shipping_cost_sar", "shipping_cost_complete");
    const paymentFees = canonical("payment_fees_sar", "payment_fees_complete");
    const knownPieces = pieces ?? count(summary?.known_pieces);
    const knownProductCost = productCost ?? amount(summary?.known_product_cost_sar);
    const knownShippingCost = shippingCost ?? amount(summary?.known_shipping_cost_sar);
    const knownPaymentFees = paymentFees ?? amount(summary?.known_payment_fees_sar);
    const computedCost = sum([productCost, shippingCost, paymentFees], { complete: true });
    const totalCost = summary?.complete === true && computedCost !== null
        && amount(summary.total_cost_sar) === computedCost ? computedCost : null;
    const financialComplete = permitted && !stats.partial && stats.orders !== null && stats.sales !== null
        && totalCost !== null && (!adSpendApplicable || spend.sar !== null && basisMatches);
    const netProfit = financialComplete ? signedMoney(stats.sales - (adSpendApplicable ? spend.sar : 0) - totalCost) : null;
    return {
        adSpendApplicable,
        spendSar: spend.sar, knownSpendSar: spend.knownSar, nativeSpend: spend.native, currency: spend.currency,
        pieces, knownPieces, productCost, knownProductCost, shippingCost, knownShippingCost,
        paymentFees, knownPaymentFees, totalCost,
        knownTotalCost: sum([knownProductCost, knownShippingCost, knownPaymentFees]),
        netProfit,
        roas: adSpendApplicable && permitted && basisMatches && stats.sales !== null && spend.sar !== null && spend.sar > 0
            ? amount(stats.sales / spend.sar) : null,
        complete: financialComplete && netProfit !== null && pieces !== null,
        provisional: spend.provisional === true,
        reasonCodes: reasonCodes(summary?.reason_codes, spend.reasonCodes,
            !summary ? ["costs_unavailable"] : [], !attributed ? ["account_attribution_incomplete"] : [],
            !timed ? ["time_assignment_incomplete"] : [], !compatible ? ["row_metrics_mismatch"] : [],
            adSpendApplicable && !basisMatches ? ["spend_basis_pending"] : [], totalCost === null ? ["costs_incomplete"] : []),
    };
}

// A proven mapped cohort is useful even when other Snapchat orders have no
// account. Keep this contribution separate from a complete account profit:
// its full account spend/fee is charged once, and no unknown order is assigned.
function withAssignedOrdersProfit(profit, row, coverage, account, basisMatches, compatible = true) {
    const unavailable = { ...profit, assignedOrdersProfit: null, assignedOrdersProfitAvailable: false };
    const summary = row?.hover_summary;
    if (profit.complete || !basisMatches || !compatible || !account
        || row?.provider !== "snapchat" || row.account_id !== account.account_id
        || row.attribution_complete !== false || row.time_assignment_complete !== true
        || row.sales_currency_conversion_complete !== true
        || coverage?.catalog_complete !== true || coverage.status !== "partial"
        || !(count(coverage.unassigned_orders) > 0)
        || count(row.known_salla_orders) === null || amount(row.known_salla_sales_sar) === null
        || summary?.basis !== "executive_order_costs_v1" || summary.currency !== "SAR"
        || summary.known_pieces_complete !== true || count(summary.known_pieces) === null
        || summary.known_total_cost_complete !== true
        || amount(profit.spendSar) === null || amount(profit.bankFees) === null) return unavailable;
    const fields = ["product_cost", "shipping_cost", "payment_fees"];
    if (fields.some((field) => summary[`known_${field}_complete`] !== true)) return unavailable;
    const costs = sum(fields.map((field) => amount(summary[`known_${field}_sar`])), { complete: true });
    if (costs === null || costs !== amount(summary.known_total_cost_sar)) return unavailable;
    const value = signedMoney(amount(row.known_salla_sales_sar) - costs - profit.spendSar - profit.bankFees);
    return value === null ? unavailable : { ...unavailable,
        assignedOrdersProfit: value, assignedOrdersProfitAvailable: true };
}

function zonedTimestamp(value) {
    return typeof value === "string" && /T.+(?:Z|[+-]\d{2}:\d{2})$/.test(value)
        && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
}

function segmentWindow(segment, day, id) {
    const midnight = Date.parse(`${day}T00:00:00+03:00`);
    if (!Number.isFinite(midnight) || new Date(midnight + 3 * HOUR_MS).toISOString().slice(0, 10) !== day) return null;
    const start = midnight + (id === "morning" ? 0 : 12 * HOUR_MS);
    const end = start + 12 * HOUR_MS;
    const cutoff = zonedTimestamp(segment?.cutoff_at);
    if (zonedTimestamp(segment?.start_at) !== start || zonedTimestamp(segment?.end_at) !== end
        || cutoff === null || cutoff < start || cutoff > end) return null;
    return { midnight, start, end, cutoff };
}

function nativeSum(values) {
    if (!values.length) return 0;
    if (values.some((value) => value === null)) return null;
    return amount(Number(values.reduce((total, value) => total + value, 0).toFixed(6)));
}

function hourlySpend(snapshot, provider, account, day, window, ready) {
    const currency = currencyCode(account?.currency) || "SAR";
    const unavailable = (reason) => ({ sar: null, knownSar: null, native: null, currency,
        provisional: window.cutoff < window.end, reasonCodes: [reason] });
    if (!ready || snapshot?.timezone !== "Asia/Riyadh") return unavailable("hourly_spend_unavailable");
    const providerState = snapshot.providers?.[provider];
    if (!account && (providerState?.hourly_available !== true || providerState?.hourly_source !== "provider_native")) {
        return unavailable(providerState?.hourly_source === "make_daily_total_marker"
            ? "daily_marker_has_no_hourly_split" : "hourly_spend_unavailable");
    }
    if (account && accountSpend(account, true) === null) return unavailable("account_spend_unavailable");
    const rows = account ? account.hourly_sar?.[day] : snapshot.hourly_spend;
    const byHour = new Map();
    (Array.isArray(rows) ? rows : []).forEach((point) => {
        if (!record(point) || point.date !== day || !Number.isInteger(point.hour_index)
            || point.hour_index < 0 || point.hour_index > 23) return;
        byHour.set(point.hour_index, byHour.has(point.hour_index) ? null : point);
    });
    const states = new Set(["confirmed", "confirmed_data", "confirmed_zero", "reconciled",
        "provisional", "provisional_data", "provisional_zero"]);
    const values = [];
    const nativeValues = [];
    let provisional = window.cutoff < window.end || account?.quality?.provisional === true
        || account?.quality?.status === "provisional" || providerState?.provisional === true
        || snapshot.spend_quality?.provisional === true || snapshot.spend_quality?.status === "provisional";
    for (let time = window.start; time < window.cutoff; time += HOUR_MS) {
        const point = byHour.get((time - window.midnight) / HOUR_MS);
        const permitted = point && (!account || states.has(point.status));
        values.push(permitted ? amount(account ? point.spend_sar : point[provider]) : null);
        nativeValues.push(permitted && account ? amount(point.spend_native) : null);
        provisional ||= Boolean(point?.status?.startsWith?.("provisional"));
    }
    const sar = values.length ? sum(values, { complete: true }) : 0;
    const knownSar = values.length ? sum(values) : 0;
    const native = account ? nativeSum(nativeValues) : sar;
    return { sar, knownSar, native: sar === null ? null : native, currency, provisional,
        samples: values.map((value, index) => ({ sar: value, native: nativeValues[index] })),
        reasonCodes: sar === null ? ["hourly_spend_incomplete"] : [] };
}

function canonicalExchangeRate(context, account, daily) {
    const rows = context.executiveSnapshot?.ads_v2?.bank_commissions?.accounts;
    const matches = (Array.isArray(rows) ? rows : []).filter((row) => record(row)
        && row.provider === "snapchat_ads" && row.external_account_id === account.account_id);
    if (!matches.length) return { observed: false, rate: null };
    if (matches.length !== 1) return { observed: true, rate: null };
    const row = matches[0];
    if (!Object.prototype.hasOwnProperty.call(row, "exchange_rate_to_sar")) return { observed: false, rate: null };
    const rate = amount(row.exchange_rate_to_sar);
    return { observed: true, rate: rate !== null && rate > 0 && row.native_currency === daily.currency
        && amount(row.spend_native) === daily.native && amount(row.spend_sar) === daily.sar ? rate : null };
}

function roundedConversionMatches(sar, native, rate) {
    const converted = native === null || rate === null ? null : amount(native * rate);
    return sar !== null && converted !== null && Math.abs(sar - converted) <= HALF_HALALA + ROUNDING_EPSILON;
}

function reconcileHourlyDay(context, provider, account) {
    const key = account || provider;
    if (context.reconciliations.has(key)) return context.reconciliations.get(key);
    const windows = SEGMENT_IDS.map((id) => {
        const rows = (context.result?.segments || []).filter((segment) => segment?.id === id);
        const segment = rows.length === 1 ? rows[0] : null;
        const window = segment?.source_only === true && segment.timezone === "Asia/Riyadh"
            && dashboardSpendPeriodMatches(segment, context.range)
            ? segmentWindow(segment, context.range.fromDate, id) : null;
        return window ? { ...window, status: segment.status } : null;
    });
    const started = windows.filter((window) => window && ["active", "completed", "complete", "partial"].includes(window.status));
    let result = { complete: false, nativeVerified: false, reasonCodes: ["hourly_daily_reconciliation_incomplete"] };
    if (windows.every(Boolean) && started.length) {
        const midnight = windows[0].midnight;
        const cutoff = Math.max(...started.map((window) => window.cutoff));
        const evidence = hourlySpend(context.spendSnapshot, provider, account, context.range.fromDate,
            { midnight, start: midnight, end: midnight + 24 * HOUR_MS, cutoff }, context.spendReady);
        const daily = dailySpend(context.spendSnapshot, provider, account, context.spendReady);
        const nativeObserved = account && evidence.native !== null && daily.native !== null;
        const nativeMatches = Boolean(nativeObserved && Math.abs(evidence.native - daily.native) <= 0.000001);
        const exchange = nativeObserved ? canonicalExchangeRate(context, account, daily) : { observed: false, rate: null };
        // The reader rounds each hour separately, then rounds the daily native
        // total. Verify that shared rate when present; never spread its rounding
        // remainder over hours or infer an FX rate from the daily ratio.
        const fxMatches = exchange.observed ? nativeMatches
            && roundedConversionMatches(daily.sar, daily.native, exchange.rate)
            && (evidence.samples || []).every((point) => roundedConversionMatches(point.sar, point.native, exchange.rate)) : null;
        const roundingBound = (evidence.samples?.length || 0) * HALF_HALALA + ROUNDING_EPSILON;
        const sarMatches = evidence.sar !== null && daily.sar !== null
            && (fxMatches === null ? Math.abs(evidence.sar - daily.sar) <= roundingBound : fxMatches);
        const mismatch = evidence.sar !== null && daily.sar !== null && !sarMatches
            || nativeObserved && !nativeMatches;
        result = { complete: sarMatches && (!nativeObserved || nativeMatches), nativeVerified: nativeMatches,
            reasonCodes: mismatch ? reasonCodes(["hourly_daily_spend_mismatch"], fxMatches === false ? ["hourly_fx_basis_mismatch"] : [])
                : sarMatches ? [] : reasonCodes(evidence.reasonCodes, ["hourly_daily_reconciliation_incomplete"]) };
    }
    context.reconciliations.set(key, result);
    return result;
}

function segmentBankFees(context, provider, account, id, spend) {
    const dailyFee = dailyBankFees(context, provider, account);
    if (dailyFee === null || dailyFee === 0) return dailyFee;
    if (spend.sar === null || !reconcileHourlyDay(context, provider, account).complete) return null;
    const rows = bankFeeRows(context, provider)?.filter((row) => !account || row.external_account_id === account.account_id);
    // A provider's hourly total cannot split fees across differently billed accounts.
    if (rows?.length !== 1) return null;
    const row = rows[0];
    const pct = amount(row.bank_commission_pct);
    if (pct === null || row.apply_bank_commission !== true) return null;
    const candidates = context.result.segments.filter((segment) => segment.id === "morning");
    const morning = candidates.length === 1 ? candidates[0] : null;
    const window = morning && segmentWindow(morning, context.range.fromDate, "morning");
    if (!window) return null;
    const afternoon = context.result.segments.find((segment) => segment.id === "afternoon");
    if (id === "morning" && afternoon?.status === "not_started") return dailyFee;
    const morningSpend = hourlySpend(context.spendSnapshot, provider, account, context.range.fromDate, window, context.spendReady);
    let morningBasis = morningSpend.sar;
    if (account) {
        const rate = amount(row.exchange_rate_to_sar);
        if (morningSpend.native === null || rate === null || rate <= 0
            || !reconcileHourlyDay(context, provider, account).nativeVerified) return null;
        morningBasis = signedMoney(morningSpend.native * rate);
    }
    if (morningBasis === null) return null;
    const morningFee = morningBasis === amount(row.spend_sar) ? dailyFee : signedMoney(morningBasis * pct / 100);
    if (morningFee === null || morningFee < 0 || morningFee > dailyFee) return null;
    // Cumulative rounding: round the verified morning basis once; the remaining
    // charge belongs to the elapsed afternoon. The server's daily charge wins,
    // so the two halves cannot charge a rounding halala twice or change spend.
    return id === "morning" ? morningFee : signedMoney(dailyFee - morningFee);
}

function unavailableSegment(id, currency, reason, status = "unavailable", segment = null, adSpendApplicable = true) {
    return { id, status, startAt: segment?.start_at || null, endAt: segment?.end_at || null,
        cutoffAt: segment?.cutoff_at || null, ...emptyStats(), adSpendApplicable,
        profit: emptyProfit(currency, reason, adSpendApplicable), reasonCodes: [reason] };
}

function segmentStats(context, provider, id, account) {
    const { result, accountsReady, spendSnapshot, range, basisMatches, spendReady } = context;
    const currency = currencyCode(account?.currency) || "SAR";
    const direct = provider === "direct";
    const unavailable = (reason, status = "unavailable", segment = null) =>
        unavailableSegment(id, currency, reason, status, segment, !direct);
    if (range.fromDate !== range.toDate) return unavailable("single_day_required");
    const candidates = accountsReady && Array.isArray(result.segments)
        ? result.segments.filter((segment) => segment?.id === id) : [];
    const segment = candidates.length === 1 ? candidates[0] : null;
    if (!segment || segment.source_only !== true || segment.timezone !== "Asia/Riyadh"
        || !dashboardSpendPeriodMatches(segment, range)) return unavailable("segment_unavailable");
    const window = segmentWindow(segment, range.fromDate, id);
    if (!window) return unavailable("segment_boundary_unverified");
    if (segment.status === "not_started" && window.cutoff === window.start) {
        return unavailable("not_started", "not_started", segment);
    }
    if (!["active", "completed", "complete", "partial"].includes(segment.status)) {
        return unavailable("segment_unavailable", "unavailable", segment);
    }
    const row = direct ? directEvidence(segment.direct_result) : account ? accountEvidence(segment.accounts, true).get(account.account_id)
        : segment.provider_results?.[provider];
    const coverageComplete = segment.coverage?.catalog_complete === true
        && segment.coverage?.status === "complete" && count(segment.coverage?.unassigned_orders) === 0;
    const observedSpend = direct ? unallocatedSpend()
        : hourlySpend(spendSnapshot, provider, account, range.fromDate, window, spendReady);
    const reconciled = direct ? { complete: true, nativeVerified: false, reasonCodes: [] }
        : reconcileHourlyDay(context, provider, account);
    const spend = { ...observedSpend,
        sar: reconciled.complete ? observedSpend.sar : null,
        native: account && account.currency !== "SAR" && !reconciled.nativeVerified ? null
            : reconciled.complete ? observedSpend.native : null,
        reasonCodes: reasonCodes(observedSpend.reasonCodes, reconciled.reasonCodes),
    };
    const timed = row?.time_assignment_complete === true;
    const attributed = direct ? row?.attribution_complete === true : account ? coverageComplete && row?.attribution_complete === true
        : row?.attribution_complete !== false;
    let stats = metrics(row, spend.sar, basisMatches, { account: direct || Boolean(account), coverageComplete: direct || coverageComplete });
    if (!timed) stats = { ...stats, orders: null, sales: null, cpa: null,
        knownOrders: count(row?.known_salla_orders), knownSales: amount(row?.known_salla_sales_sar), partial: true };
    const profit = withAssignedOrdersProfit(withBankFees(profitMetrics(stats, row?.hover_summary, spend, basisMatches,
        { attributed, timed, adSpendApplicable: !direct }), segmentBankFees(context, provider, account, id, spend)),
        row, segment.coverage, account, basisMatches);
    return { id, status: profit.complete ? window.cutoff < window.end ? "active" : "completed" : "partial",
        startAt: segment.start_at, endAt: segment.end_at, cutoffAt: segment.cutoff_at,
        ...stats, adSpendApplicable: !direct, profit, reasonCodes: reasonCodes(profit.reasonCodes, row?.reason_codes) };
}

function withDetails(stats, context, provider, id = null) {
    const direct = provider === "direct";
    const account = id === null ? null : context.spendsById.get(id)
        || { account_id: id, currency: context.displayed.get(id)?.currency };
    const source = direct ? directEvidence(context.result?.direct_result)
        : id === null ? context.result?.provider_results?.[provider] : context.resultsById.get(id);
    const row = context.accountsReady && record(source) ? source : null;
    const spend = direct ? unallocatedSpend() : dailySpend(context.spendSnapshot, provider, account, context.spendReady);
    const attributed = direct ? row?.attribution_complete === true : id === null ? row?.attribution_complete !== false
        : context.coverageComplete && row?.attribution_complete === true;
    const compatible = record(row) && (stats.orders === null || count(row.salla_orders) === stats.orders)
        && (stats.sales === null || amount(row.salla_sales_sar) === stats.sales);
    return { ...stats, adSpendApplicable: !direct,
        profit: withAssignedOrdersProfit(withBankFees(profitMetrics(stats, row?.hover_summary, spend, context.basisMatches, { attributed, compatible,
            timed: !direct || row?.time_assignment_complete === true, adSpendApplicable: !direct }), dailyBankFees(context, provider, account)),
            row, context.result?.coverage, account, context.basisMatches, compatible),
        segments: SEGMENT_IDS.map((segmentId) => segmentStats(context, provider, segmentId, account)),
    };
}

function unassignedRowStats(container) {
    const row = container?.unassigned_result;
    const orders = count(container?.coverage?.unassigned_orders);
    if (!record(row) || row.provider !== "snapchat" || row.cohort_basis !== "snapchat_unassigned_orders"
        || row.account_id != null || row.attribution_complete !== false || row.ad_spend_allocation_complete !== false
        || orders === null || count(row.known_salla_orders) !== orders) return null;
    const timed = row.time_assignment_complete === true;
    const knownSales = amount(row.known_salla_sales_sar);
    const stats = { orders: timed ? orders : null,
        sales: timed && row.sales_currency_conversion_complete === true ? knownSales : null,
        cpa: null, knownOrders: orders, knownSales,
        partial: !timed || row.sales_currency_conversion_complete !== true || knownSales === null };
    // This is a source-identified cohort, not another advertising account.
    // Costs remain known subtotals; neither spend nor bank fees can be allocated.
    const profit = withBankFees(profitMetrics(stats, row.hover_summary, unallocatedSpend(), false,
        { attributed: false, timed }), null);
    return { ...stats, accountUnassigned: true, adSpendApplicable: true, profit };
}

function withUnassignedDetails(context) {
    if (!context.accountMode || !context.accountsReady) return null;
    const stats = unassignedRowStats(context.result);
    if (!stats || stats.knownOrders === 0) return null;
    return { ...stats, segments: SEGMENT_IDS.map((id) => {
        const unavailable = (reason, status = "unavailable", segment = null) =>
            unavailableSegment(id, "SAR", reason, status, segment);
        if (context.range.fromDate !== context.range.toDate) return unavailable("single_day_required");
        const candidates = Array.isArray(context.result.segments)
            ? context.result.segments.filter((segment) => segment?.id === id) : [];
        const segment = candidates.length === 1 ? candidates[0] : null;
        if (!segment || segment.source_only !== true || segment.timezone !== "Asia/Riyadh"
            || !dashboardSpendPeriodMatches(segment, context.range)) return unavailable("segment_unavailable");
        const window = segmentWindow(segment, context.range.fromDate, id);
        if (!window) return unavailable("segment_boundary_unverified");
        if (segment.status === "not_started" && window.cutoff === window.start) {
            return unavailable("not_started", "not_started", segment);
        }
        if (!["active", "completed", "complete", "partial"].includes(segment.status)) {
            return unavailable("segment_unavailable", "unavailable", segment);
        }
        const period = unassignedRowStats(segment);
        return period ? { ...period, id, status: "partial", startAt: segment.start_at, endAt: segment.end_at,
            cutoffAt: segment.cutoff_at, reasonCodes: period.profit.reasonCodes }
            : unavailable("unassigned_cohort_unverified", "unavailable", segment);
    }) };
}

function aggregateProfit(stats, entries, basisMatches) {
    const total = (field, integer = false, complete = true) => sum(entries.map((entry) => entry[field]), { integer, complete });
    const paid = entries.filter((entry) => entry.adSpendApplicable !== false);
    const spendSar = sum(paid.map((entry) => entry.spendSar), { complete: true });
    const totalCost = total("totalCost");
    const financialComplete = !stats.partial && basisMatches && spendSar !== null && totalCost !== null
        && entries.every((entry) => entry.complete);
    const netProfit = financialComplete ? signedMoney(stats.sales - spendSar - totalCost) : null;
    return {
        adSpendApplicable: true,
        spendSar, knownSpendSar: sum(paid.map((entry) => entry.knownSpendSar)), nativeSpend: spendSar, currency: "SAR",
        pieces: total("pieces", true), knownPieces: total("knownPieces", true, false),
        productCost: total("productCost"), knownProductCost: total("knownProductCost", false, false),
        shippingCost: total("shippingCost"), knownShippingCost: total("knownShippingCost", false, false),
        paymentFees: total("paymentFees"), knownPaymentFees: total("knownPaymentFees", false, false),
        bankFees: total("bankFees"), knownBankFees: total("knownBankFees", false, false),
        totalCost, knownTotalCost: total("knownTotalCost", false, false), netProfit,
        roas: basisMatches && stats.sales !== null && spendSar !== null && spendSar > 0 ? amount(stats.sales / spendSar) : null,
        complete: financialComplete && netProfit !== null,
        provisional: entries.some((entry) => entry.provisional),
        reasonCodes: reasonCodes(...entries.map((entry) => entry.reasonCodes)),
    };
}

function sourceTotalProfit(stats, rows, context, source, { timed = true } = {}) {
    const fallback = aggregateProfit(stats, rows.map((row) => row.profit), context.basisMatches);
    const sourceAccounts = context.resultsById;
    const scopeMatches = context.accountMode && context.accountsReady
        && sourceAccounts.size === context.displayed.size
        && [...sourceAccounts].every(([id, row]) => row !== null && context.displayed.has(id));
    if (!scopeMatches || !record(source) || !rows.every((row) => row.profit.complete)) return fallback;
    // Cohort-level gateway rounding belongs to the server. It is never spread
    // over account rows, or borrowed by a subset of the displayed accounts.
    const compatible = count(source.salla_orders) === stats.orders && amount(source.salla_sales_sar) === stats.sales;
    return withBankFees(profitMetrics(stats, source.hover_summary, {
        sar: fallback.spendSar, knownSar: fallback.knownSpendSar, native: fallback.spendSar,
        currency: "SAR", provisional: fallback.provisional, reasonCodes: fallback.reasonCodes,
    }, context.basisMatches, { attributed: source.attribution_complete === true,
        timed, compatible }), fallback.bankFees, fallback.knownBankFees);
}

function strictAccountSet(rows, providerRequired = false) {
    if (!Array.isArray(rows)) return null;
    const indexed = accountEvidence(rows, providerRequired);
    return indexed.size === rows.length && [...indexed.values()].every(Boolean) ? indexed : null;
}

function sameAccountSet(left, right) {
    return left !== null && right !== null && left.size === right.size && [...left.keys()].every((id) => right.has(id));
}

function financialAccountScopeMatches(context, coverage = context.result?.coverage) {
    const source = strictAccountSet(context.result?.accounts, true);
    const saved = strictAccountSet(context.spendSnapshot?.providers?.snapchat?.accounts);
    return count(coverage?.outside_account_scope_orders) === 0
        && sameAccountSet(source, saved) && (!context.accountMode || sameAccountSet(source, context.displayed));
}

// This total is proved by the disjoint Salla cohorts in this response. It is
// never filled with the executive headline, or by relabeling unmatched ads.
function verifiedCohortTotal(container, source) {
    if (!record(source) || source.cohort_basis !== "executive_filtered_orders"
        || source.attribution_complete !== true) return null;
    const accounts = strictAccountSet(container?.accounts, true);
    const direct = directEvidence(container?.direct_result);
    const providers = ["google", "tiktok", "meta"].map((provider) => {
        const row = container?.provider_results?.[provider];
        return record(row) && row.provider === provider ? row : null;
    });
    if (accounts === null || direct === null || providers.some((row) => row === null)) return null;
    const rows = [...accounts.values(), ...providers, direct];
    const unrepresented = count(container.coverage?.unrepresented_orders);
    const expectedUnrepresented = sum([count(container.coverage?.unassigned_orders),
        count(container.coverage?.outside_account_scope_orders)], { complete: true, integer: true });
    if (unrepresented === null || expectedUnrepresented === null || unrepresented !== expectedUnrepresented) return null;
    const knownOrders = count(source.known_salla_orders);
    const observedOrders = sum([...rows.map((row) => count(row.known_salla_orders)), unrepresented], { complete: true, integer: true });
    if (knownOrders === null || observedOrders === null || knownOrders !== observedOrders
        || source.salla_orders !== null && count(source.salla_orders) !== knownOrders) return null;
    const knownSales = amount(source.known_salla_sales_sar);
    const observedSales = sum(rows.map((row) => amount(row.known_salla_sales_sar)), { complete: true });
    const roundingBound = rows.length * HALF_HALALA + ROUNDING_EPSILON;
    if (knownSales === null || observedSales === null || knownSales + roundingBound < observedSales
        || unrepresented === 0 && Math.abs(knownSales - observedSales) > roundingBound
        || source.salla_sales_sar !== null && (source.sales_currency_conversion_complete !== true
            || amount(source.salla_sales_sar) !== knownSales)) return null;
    return source;
}

function cohortMetrics(source, spend, basisMatches) {
    const stats = metrics(source, spend.sar, basisMatches, { account: true });
    return source.time_assignment_complete === true ? { ...stats, cpa: stats.partial ? null : stats.cpa } : { ...stats,
        orders: null, sales: null, cpa: null, partial: true };
}

function totalSpendEvidence(context, entries = null, coverage = context.result?.coverage) {
    const paid = entries?.filter((entry) => entry.adSpendApplicable !== false);
    const sar = paid ? sum(paid.map((entry) => entry.profit.spendSar), { complete: true })
        : context.spendReady ? amount(dashboardAvailableAmount(context.spendSnapshot, context.range)) : null;
    const knownSar = paid ? sum(paid.map((entry) => entry.profit.knownSpendSar))
        : sar ?? dashboardKnownIncompleteAmount(context.spendSnapshot, context.range);
    return { sar, knownSar, native: sar, currency: "SAR",
        provisional: paid ? paid.some((entry) => entry.profit.provisional)
            : context.spendSnapshot?.spend_quality?.provisional === true
                || context.spendSnapshot?.spend_quality?.status === "provisional",
        reasonCodes: reasonCodes(paid ? paid.flatMap((entry) => entry.profit.reasonCodes) : [],
            sar === null ? ["spend_unavailable"] : [],
            !financialAccountScopeMatches(context, coverage) ? ["displayed_account_scope_mismatch"] : []),
    };
}

function withTotalDetails(stats, rows, context) {
    const { basisMatches } = context;
    const bankFees = totalBankFees(context);
    const verifyBankFeeBasis = (profit) => bankFees !== null ? profit : { ...profit,
        bankFees: null, totalCost: null, netProfit: null, complete: false,
        reasonCodes: reasonCodes(profit.reasonCodes, ["ad_bank_fees_unverified"]),
    };
    const advertisedCohort = context.accountsReady && record(context.result?.daily_total)
        && Object.prototype.hasOwnProperty.call(context.result.daily_total, "cohort_basis");
    const dailySource = context.accountsReady ? verifiedCohortTotal(context.result, context.result.daily_total) : null;
    const financialBasis = basisMatches && financialAccountScopeMatches(context);
    const dailySpend = totalSpendEvidence(context);
    const total = dailySource ? cohortMetrics(dailySource, dailySpend, financialBasis)
        : advertisedCohort ? { ...stats, orders: null, sales: null, cpa: null, partial: true } : stats;
    const profit = verifyBankFeeBasis(dailySource ? withBankFees(profitMetrics(total, dailySource.hover_summary, dailySpend, financialBasis,
        { timed: dailySource.time_assignment_complete === true }), bankFees,
        sum(rows.map((row) => row.profit.knownBankFees)))
        : advertisedCohort ? emptyProfit("SAR", "cohort_total_unverified")
            : sourceTotalProfit(total, rows, context, context.result?.daily_total));
    return { ...total, adSpendApplicable: true, profit,
        segments: SEGMENT_IDS.map((id) => {
            const entries = rows.map((row) => row.segments.find((segment) => segment.id === id));
            const metrics = totalMetrics(entries, entries.filter((entry) => entry.adSpendApplicable !== false)
                .map((entry) => entry.profit.spendSar), basisMatches);
            const sources = Array.isArray(context.result?.segments)
                ? context.result.segments.filter((segment) => segment?.id === id) : [];
            const segment = sources.length === 1 ? sources[0] : null;
            const source = segment?.total;
            if (advertisedCohort) {
                if (context.range.fromDate !== context.range.toDate) return unavailableSegment(id, "SAR", "single_day_required");
                if (!dailySource || segment?.source_only !== true || segment.timezone !== "Asia/Riyadh"
                    || !dashboardSpendPeriodMatches(segment, context.range)) return unavailableSegment(id, "SAR", "cohort_total_unverified");
                const window = segmentWindow(segment, context.range.fromDate, id);
                if (!window) return unavailableSegment(id, "SAR", "segment_boundary_unverified");
                if (segment.status === "not_started" && window.cutoff === window.start) {
                    return unavailableSegment(id, "SAR", "not_started", "not_started", segment);
                }
                const proven = verifiedCohortTotal(segment, source);
                if (!proven || !["active", "completed", "complete", "partial"].includes(segment.status)) {
                    return unavailableSegment(id, "SAR", "cohort_total_unverified");
                }
                const segmentFinancialBasis = basisMatches && financialAccountScopeMatches(context, segment.coverage);
                const spend = totalSpendEvidence(context, entries, segment.coverage);
                const stats = cohortMetrics(proven, spend, segmentFinancialBasis);
                const profit = verifyBankFeeBasis(withBankFees(profitMetrics(stats, proven.hover_summary, spend, segmentFinancialBasis,
                    { timed: proven.time_assignment_complete === true }),
                    sum(entries.map((entry) => entry.profit.bankFees), { complete: true }),
                    sum(entries.map((entry) => entry.profit.knownBankFees))));
                return { id, status: profit.complete ? window.cutoff < window.end ? "active" : "completed" : "partial",
                    startAt: segment.start_at, endAt: segment.end_at, cutoffAt: segment.cutoff_at,
                    ...stats, adSpendApplicable: true, profit, reasonCodes: profit.reasonCodes };
            }
            const profit = verifyBankFeeBasis(sourceTotalProfit(metrics, entries, context, source,
                { timed: source?.time_assignment_complete === true }));
            const status = entries.every((entry) => entry.status === "not_started") ? "not_started"
                : entries.every((entry) => entry.status === "unavailable") ? "unavailable"
                : profit.complete ? entries.some((entry) => entry.status === "active") ? "active" : "completed" : "partial";
            return { id, status, startAt: entries[0]?.startAt || null, endAt: entries[0]?.endAt || null,
                cutoffAt: entries[0]?.cutoffAt || null, ...metrics, adSpendApplicable: true, profit,
                reasonCodes: reasonCodes(profit.reasonCodes, ...entries.map((entry) => entry.reasonCodes)) };
        }),
    };
}

/** Display-only Salla metrics. This reads existing snapshots and never fetches or writes. */
export function buildDashboardAdsTableMetrics({
    executiveSnapshot,
    spendSnapshot,
    fromDate,
    toDate,
    snapchatAccounts = null,
} = {}) {
    const range = { fromDate, toDate: toDate || fromDate };
    const accountMode = Array.isArray(snapchatAccounts);
    const displayed = displayedAccounts(snapchatAccounts);
    const accounts = new Map([...displayed.keys()].map((id) => [id, emptyStats()]));
    const providers = Object.fromEntries(DASHBOARD_ADS_PROVIDERS.map((provider) => [provider, emptyStats()]));
    const executiveReady = record(executiveSnapshot) && dashboardSpendPeriodMatches({
        date_from: executiveSnapshot.range?.from_date,
        date_to: executiveSnapshot.range?.to_date,
    }, range);
    if (!executiveReady) {
        const pendingStats = (currency = "SAR", adSpendApplicable = true) => ({ ...emptyStats(), adSpendApplicable,
            profit: emptyProfit(currency, "executive_period_pending", adSpendApplicable),
            segments: SEGMENT_IDS.map((id) => unavailableSegment(id, currency,
                range.fromDate !== range.toDate ? "single_day_required" : "executive_period_pending", "unavailable", null, adSpendApplicable)) });
        accounts.forEach((_, id) => accounts.set(id, pendingStats(currencyCode(displayed.get(id)?.currency) || "SAR")));
        DASHBOARD_ADS_PROVIDERS.forEach((provider) => { providers[provider] = pendingStats(); });
        return { accounts, providers, direct: pendingStats("SAR", false), unassigned: null, total: pendingStats(),
            unassignedOrders: null, unrepresentedOrders: null, pending: true };
    }

    const spendReady = dashboardSpendPeriodMatches(spendSnapshot, range);
    let basisMatches = false;
    try {
        basisMatches = spendReady && amount(spendSnapshot?.total_sar) !== null
            && amount(executiveSnapshot?.totals?.total_ads_cost) !== null
            && dashboardAvailableAmount(spendSnapshot, range) !== null
            && dashboardCanonicalSpendMatches(executiveSnapshot, spendSnapshot);
    } catch {
        // Malformed basis evidence must not hide independently known Salla facts.
    }

    const providerRows = executiveSnapshot.ads_v2?.executive_breakdown?.providers;
    const providerSpends = new Map();
    DASHBOARD_ADS_PROVIDERS.forEach((provider) => {
        const spend = providerSpend(spendSnapshot, provider, spendReady);
        providerSpends.set(provider, spend);
        providers[provider] = metrics(providerRows?.[provider], spend, basisMatches);
    });

    const result = executiveSnapshot.ads_v2?.account_results;
    const accountsReady = record(result) && result.source_only === true
        && result.timezone === "Asia/Riyadh" && dashboardSpendPeriodMatches(result, range)
        && Array.isArray(result.accounts);
    const unassignedOrders = accountsReady ? count(result.coverage?.unassigned_orders) : null;
    const unrepresentedOrders = accountsReady ? count(result.coverage?.unrepresented_orders) ?? unassignedOrders : null;
    const coverageComplete = accountsReady && result.coverage?.catalog_complete === true
        && result.coverage?.status === "complete" && unassignedOrders === 0;
    const resultsById = accountsReady ? accountEvidence(result.accounts, true) : new Map();
    const spendsById = accountEvidence(spendSnapshot?.providers?.snapchat?.accounts);
    const accountSpends = [];
    displayed.forEach((_, id) => {
        const spend = accountSpend(spendsById.get(id), spendReady);
        accountSpends.push(spend);
        accounts.set(id, accountsReady
            ? metrics(resultsById.get(id), spend, basisMatches, { account: true, coverageComplete })
            : emptyStats());
    });

    const context = { executiveSnapshot, spendSnapshot, range, basisMatches, spendReady, accountsReady, accountMode,
        result, resultsById, spendsById, displayed, coverageComplete, reconciliations: new Map(), bankFeeRows: new Map() };
    accounts.forEach((stats, id) => accounts.set(id, withDetails(stats, context, "snapchat", id)));
    DASHBOARD_ADS_PROVIDERS.forEach((provider) => {
        providers[provider] = withDetails(providers[provider], context, provider);
    });
    const direct = withDetails(accountsReady ? metrics(directEvidence(result.direct_result), null, false,
        { account: true }) : emptyStats(), context, "direct");
    const visibleProviders = DASHBOARD_ADS_PROVIDERS.filter((provider) => !accountMode || provider !== "snapchat");
    const rows = [...accounts.values(), ...visibleProviders.map((provider) => providers[provider]), direct];
    const spends = [...accountSpends, ...visibleProviders.map((provider) => providerSpends.get(provider))];
    return {
        accounts,
        providers,
        direct,
        // The verified all-order total already includes this cohort. Keep it
        // out of the account/spend aggregation to avoid counting it twice.
        unassigned: withUnassignedDetails(context),
        total: withTotalDetails(totalMetrics(rows, spends, basisMatches), rows, context),
        unassignedOrders,
        unrepresentedOrders,
        pending: accountMode && displayed.size > 0 && !accountsReady,
    };
}
