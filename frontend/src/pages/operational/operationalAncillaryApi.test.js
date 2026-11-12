import http from '../../lib/api';
import {operationalApi as api} from './api';
jest.mock('../../lib/api', () => ({get: jest.fn(), post: jest.fn()}));
beforeEach(() => {jest.resetAllMocks(); http.get.mockResolvedValue({data: {items: []}});});

test.each([
  ['salaryStartContext', [], '/salary-start/context'],
  ['inventoryOpeningContext', [], '/inventory-opening/context'],
  ['inventoryAvailability', [], '/inventory'],
  ['inventoryCatalog', [true], '/inventory-catalog?configured=true'],
  ['inventoryPurchaseEntry', ['supplier/a', 'INV 1'], '/inventory-purchases/entry/supplier%2Fa/INV%201'],
  ['entities', ['supplier'], '/entities/supplier'],
  ['providerHistoryReviews', [], '/provider-history-reviews'],
])('%s forwards the read cancellation signal without a financial write', async (name, args, path) => {
  const controller = new AbortController();
  await api[name](...args, {signal: controller.signal});
  expect(http.get).toHaveBeenCalledWith(`/operational-balances${path}`, {signal: controller.signal});
  expect(http.post).not.toHaveBeenCalled();
});

test('obligation cancellation config cannot replace the selected party', async () => {
  const controller = new AbortController();
  await api.obligations('supplier', 'selected', {signal: controller.signal, params: {party_id: 'other'}});
  expect(http.get).toHaveBeenCalledWith('/operational-balances/obligations', {
    signal: controller.signal, params: {party_type: 'supplier', party_id: 'selected'},
  });
});
