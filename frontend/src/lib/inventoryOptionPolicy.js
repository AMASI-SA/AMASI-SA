import {pruneInventoryConfiguration} from './inventoryProductConfiguration';

const configurationFields = ['options', 'configuration_issues', 'selection_combinations',
  'configuration_contract_version', 'physical_option_ids', 'inventory_option_policy_revision'];

export const inventoryOptionPolicyRevision = product => Number.isSafeInteger(product?.inventory_option_policy_revision)
  ? product.inventory_option_policy_revision : 0;

export function inventoryOptionPolicyChanged(line, current) {
  return line?.kind === 'product' && !!current
    && inventoryOptionPolicyRevision(line) !== inventoryOptionPolicyRevision(current);
}

// Apply only to the open input or an explicitly reopened draft line. Approved
// rows and other saved draft lines retain their original description and data.
export function applyInventoryOptionDefinition(line, product) {
  const next = {...line};
  for (const key of configurationFields) {
    if (key in product) next[key] = product[key];
    else if (key === 'inventory_option_policy_revision') next[key] = 0;
  }
  if (line.purchase_configuration) {
    const config = {...line.purchase_configuration};
    if (next.configuration_contract_version === 2 && !('deferred_option_ids' in config)) config.deferred_option_ids = [];
    next.purchase_configuration = pruneInventoryConfiguration(next, config);
    delete next.purchase_option_labels;
    delete next.deferred_option_labels;
  }
  return next;
}
