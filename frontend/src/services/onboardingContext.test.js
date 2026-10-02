import { loadOnboardingContext, stageSourceErrors } from './onboardingContext';

const sections = ['banks_cash', 'providers', 'couriers_cod', 'inventory', 'suppliers', 'payroll_obligations', 'equity'];
const transport = () => ({
    getOnboardingDefinitions: async () => ({schema_version: 1, sections, financial_base: '/api/financial-provider-apps/accounting-module/financial-accounts', ssot_setup_version: 1}),
    listOnboardingSessions: async () => ({items: [{id: 'saved'}]}),
    getOnboardingIdentities: async kind => ({items: [{id: kind, label: kind}]}),
    getOnboardingFinancialAccounts: async () => ({items: [{id: 'bank-exact', label: 'bank', status: 'active', account_type: 'bank', currency: 'SAR'}]}),
});

test('one failed identity source preserves other catalogues and blocks only its shared financial section', async () => {
    const t = transport(); t.getOnboardingIdentities = async kind => { if (kind === 'employee') throw new Error('SECRET'); return {items: [{id: kind, label: kind}]}; };
    const result = await loadOnboardingContext(t);
    expect(result.context.entities.financial_accounts[0].id).toBe('bank-exact');
    expect(result.sessions).toEqual([{id: 'saved'}]);
    expect(result.errors.map(e => e.source)).toEqual(['employee']);
    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(stageSourceErrors('employees', result.errors)).toHaveLength(1);
    expect(stageSourceErrors('banks', result.errors)).toEqual([]);
    expect(stageSourceErrors('review', result.errors)).toHaveLength(1);
});

test('accounts failure is different from an empty successful source and does not conceal inventory or employees', async () => {
    const t = transport(); t.getOnboardingFinancialAccounts = async () => { throw new Error('offline'); };
    const result = await loadOnboardingContext(t);
    expect(result.context.entities.employees[0].id).toBe('employee');
    expect(result.errors.map(e => e.source)).toEqual(['financial_accounts']);
    expect(stageSourceErrors('banks', result.errors)).toHaveLength(1);
    expect(stageSourceErrors('inventory', result.errors)).toEqual([]);
    expect(stageSourceErrors('employees', result.errors)).toEqual([]);
    expect((await loadOnboardingContext({...transport(), getOnboardingFinancialAccounts: async () => ({items: []})})).errors).toEqual([]);
});

test('missing provider binding source disables both provider and advertising editors; unknown definitions fail closed', async () => {
    expect(stageSourceErrors('providers', [{source: 'ad_account'}])).toHaveLength(1);
    expect(stageSourceErrors('advertising', [{source: 'provider'}])).toHaveLength(1);
    const t = transport(); t.getOnboardingDefinitions = async () => ({schema_version: 9});
    t.getOnboardingFinancialAccounts = jest.fn();
    const result = await loadOnboardingContext(t);
    expect(result.context).toBeNull(); expect(result.errors[0].source).toBe('definitions');
    expect(t.getOnboardingFinancialAccounts).not.toHaveBeenCalled();
});
