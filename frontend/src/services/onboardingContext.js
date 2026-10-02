import { onboardingErrorMessage } from './accountingOnboarding';

export const FINANCIAL_SECTIONS = ['banks_cash', 'providers', 'couriers_cod', 'inventory', 'suppliers', 'payroll_obligations', 'equity'];
const KINDS = {bank: 'banks', provider: 'payment_providers', employee: 'employees', supplier: 'suppliers', external_person: 'external_persons', courier: 'couriers', store_driver: 'store_drivers', ad_account: 'ad_accounts'};
const LABELS = {definitions: 'تعريفات التأسيس', sessions: 'الجلسات المحفوظة', financial_accounts: 'الحسابات المالية والبنوك والصناديق', bank: 'دليل البنوك', provider: 'مزودي الدفع', employee: 'الموظفين', supplier: 'الموردين', external_person: 'الأطراف', courier: 'شركات الشحن', store_driver: 'المندوبين', ad_account: 'حسابات الإعلان'};
const active = a => a.status === 'active' && !['archived', 'is_archived', 'deleted', 'is_deleted'].some(k => a[k] === true) && !['active', 'is_active'].some(k => a[k] === false);
const items = result => {
    if (!Array.isArray(result?.items)) throw new Error('onboarding_response_invalid');
    return result.items;
};

// A rejected source is never represented as a successful empty catalogue. Each
// consumer also receives errors and must disable writes that depend on it.
export async function loadOnboardingContext(transport) {
    const sources = ['definitions', 'sessions', ...Object.keys(KINDS)];
    const reads = [() => transport.getOnboardingDefinitions(), () => transport.listOnboardingSessions(), ...Object.keys(KINDS).map(kind => () => transport.getOnboardingIdentities(kind))];
    const settled = await Promise.allSettled(reads.map(read => Promise.resolve().then(read)));
    const data = {}, errors = [];
    const failure = (source, err) => errors.push({source, label: LABELS[source], message: onboardingErrorMessage(err)});
    settled.forEach((result, i) => {
        const source = sources[i];
        if (result.status === 'rejected') failure(source, result.reason);
        else {
            try { data[source] = source === 'definitions' ? result.value : items(result.value); }
            catch (err) { failure(source, err); }
        }
    });
    const definitions = data.definitions;
    if (!definitions || definitions.schema_version !== 1 || !FINANCIAL_SECTIONS.every(id => definitions.sections?.includes(id))) {
        if (!errors.some(e => e.source === 'definitions')) failure('definitions', new Error('onboarding_response_invalid'));
        return {context: null, sessions: data.sessions || [], errors};
    }
    let accounts = [];
    try { accounts = items(await transport.getOnboardingFinancialAccounts(definitions.financial_base)).map(a => ({...a, name: a.name || a.label})); }
    catch (err) { failure('financial_accounts', err); }
    const entities = Object.fromEntries(Object.entries(KINDS).map(([kind, key]) => [key, (data[kind] || []).map(item => ({...item, name: item.name || item.label}))]));
    entities.financial_accounts = accounts.filter(a => active(a) && ['bank', 'cash', 'overdraft'].includes(a.account_type));
    entities.banks = accounts.filter(a => active(a) && a.account_type === 'bank' && a.currency === 'SAR');
    const categories = Object.entries(definitions.opening_categories || {}).map(([id, info]) => ({id, ...info}));
    return {sessions: data.sessions || [], errors, context: {
        financial_base: definitions.financial_base, entities, financial_accounts: accounts,
        classifications: {prepaid: categories.filter(c => c.id === 'prepaid_expense'), obligations: categories.filter(c => ['accrued_expense', 'other_receivable', 'other_payable', 'input_vat', 'sales_vat_payable'].includes(c.id))},
        feeConfigurationSupported: definitions.ssot_setup_version === 1, ssotSetupSupported: definitions.ssot_setup_version === 1,
    }};
}

// Shared section saves preserve siblings: do not permit a partial catalogue to
// reinterpret or remove a sibling's exact canonical binding.
export function stageSourceErrors(stage, errors) {
    const dependencies = {
        banks: ['financial_accounts'], providers: ['provider', 'ad_account', 'financial_accounts'],
        advertising: ['provider', 'ad_account', 'financial_accounts'], payment_fees: ['provider', 'ad_account', 'financial_accounts'],
        employees: ['employee'], suppliers: ['supplier', 'external_person'], external_persons: ['supplier', 'external_person'],
        courier_contracts: ['courier', 'financial_accounts'], courier_balances: ['courier', 'store_driver'], drivers: ['courier', 'store_driver'],
    };
    return errors.filter(e => e.source === 'definitions' || (stage === 'review' ? e.source !== 'sessions' : (dependencies[stage] || []).includes(e.source)));
}
