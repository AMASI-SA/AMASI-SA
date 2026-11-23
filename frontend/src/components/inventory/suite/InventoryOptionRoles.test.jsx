import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import InventoryOptionRoles from './InventoryOptionRoles';

const definition = {
  id: 'product', kind: 'product', name: 'منتج تجريبي', configuration_contract_version: 2,
  inventory_option_policy_revision: 0,
  options: [
    {id: 'size', name: 'المقاس', type: 'select', inventory_role: 'physical', role_locked: true, stock_identity: true, values: [{id: 'small', name: 'صغير'}]},
    {id: 'gift', name: 'هل تريد كرت إهداء', type: 'radio', inventory_role: 'customization', values: [{id: 'yes', name: 'نعم'}, {id: 'no', name: 'لا'}]},
    {id: 'card', name: 'نص كرت الإهداء', type: 'textarea', inventory_role: 'customization', visibility_rules: [{option_id: 'gift', value_id: 'yes', operator: '='}], values: []},
    {id: 'name', name: 'الاسم المطرّز', type: 'text', inventory_role: 'customization', values: []},
  ],
};
const policy = () => ({product_id: 'product', revision: 0, catalog_fingerprint: 'catalog-before',
  roles: {size: 'physical', gift: 'customization', card: 'customization', name: 'customization'},
  options: definition.options, product: definition});
let host, root, loadPolicy, savePolicy, onApply, onBusyChange, onEditingChange;
const render = async (props = {}) => act(async () => root.render(<InventoryOptionRoles product={definition}
  loadPolicy={loadPolicy} savePolicy={savePolicy} onApply={onApply}
  onBusyChange={onBusyChange} onEditingChange={onEditingChange} {...props}/>));
const click = async label => act(async () => {
  const button = [...host.querySelectorAll('button')].find(node => node.textContent === label);
  if (!button) throw Error('Missing button: '+label);
  button.click();
});
const roleSelect = name => host.querySelector(`[aria-label="تصنيف ${name}"]`);
const change = async (name, value) => act(async () => {
  const element = roleSelect(name);
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(element, value);
  element.dispatchEvent(new Event('change', {bubbles: true}));
});
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  loadPolicy = jest.fn().mockResolvedValue(policy()); savePolicy = jest.fn(); onApply = jest.fn();
  onBusyChange = jest.fn(); onEditingChange = jest.fn();
});
afterEach(async () => {await act(async () => root.unmount()); host.remove();});

test('all choice and text fields can be classified, while a physical variant stays locked', async () => {
  await render(); expect(loadPolicy).not.toHaveBeenCalled();
  await click('تصنيف الخيارات');
  expect(loadPolicy).toHaveBeenCalledWith('product');
  expect(roleSelect('المقاس').disabled).toBe(true);
  expect(roleSelect('نص كرت الإهداء').options).toHaveLength(3);
  expect(roleSelect('الاسم المطرّز').value).toBe('customization');
  expect(host.textContent).toContain('حقل نص');
  expect(onEditingChange).toHaveBeenLastCalledWith(true);
});

test('a side question and its dependent text are saved together with the exact revision and catalog identity', async () => {
  await render(); await click('تصنيف الخيارات');
  await change('هل تريد كرت إهداء', 'informational');
  const save = [...host.querySelectorAll('button')].find(node => node.textContent === 'حفظ التصنيف');
  expect(save.disabled).toBe(true);
  expect(host.textContent).toContain('نص كرت الإهداء');
  await change('نص كرت الإهداء', 'informational');
  expect(save.disabled).toBe(false);
  const result = {...policy(), revision: 1, roles: {...policy().roles, gift: 'informational', card: 'informational'}, product:{...definition,inventory_option_policy_revision:1}};
  savePolicy.mockResolvedValue(result);
  await click('حفظ التصنيف');
  expect(savePolicy).toHaveBeenCalledWith('product', {expected_revision: 0, catalog_fingerprint: 'catalog-before', roles: result.roles});
  expect(onApply).toHaveBeenCalledWith(result);
  expect(onEditingChange).toHaveBeenLastCalledWith(false);
  expect(host.textContent).toContain('حُفظ التصنيف لهذا المنتج');
});

test('uncertain or stale writes keep the choices for review and do not change the current stock line', async () => {
  await render(); await click('تصنيف الخيارات');
  await change('نص كرت الإهداء', 'informational');
  savePolicy.mockRejectedValue({response: {status: 409, data: {detail: {message: 'تغير التصنيف؛ أعد تحميله.'}}}});
  await click('حفظ التصنيف');
  expect(roleSelect('نص كرت الإهداء').value).toBe('informational');
  expect(onApply).not.toHaveBeenCalled();
  expect(host.textContent).toContain('تغير التصنيف؛ أعد تحميله.');
  expect([...host.querySelectorAll('button')].find(node => node.textContent === 'حفظ التصنيف').disabled).toBe(true);
  const latest = {...policy(), revision: 2, catalog_fingerprint: 'catalog-after',product:{...definition,inventory_option_policy_revision:2}};
  loadPolicy.mockResolvedValue(latest);
  await click('تحميل التصنيف الحالي');
  expect(roleSelect('نص كرت الإهداء').value).toBe('customization');
  savePolicy.mockResolvedValue(latest);
  await click('حفظ التصنيف');
  expect(savePolicy).toHaveBeenLastCalledWith('product', {expected_revision: 2, catalog_fingerprint: 'catalog-after', roles: latest.roles});
});

test('disabled and in-flight controls never submit duplicate policy saves', async () => {
  await render({disabled: true}); await click('تصنيف الخيارات'); expect(loadPolicy).not.toHaveBeenCalled();
  await render(); await click('تصنيف الخيارات');
  let finish; savePolicy.mockReturnValue(new Promise(resolve => {finish = resolve;}));
  await click('حفظ التصنيف'); await click('حفظ التصنيف');
  expect(savePolicy).toHaveBeenCalledTimes(1); expect(onBusyChange).toHaveBeenLastCalledWith(true);
  await act(async () => finish(policy()));
  expect(onBusyChange).toHaveBeenLastCalledWith(false);
});

test('canceling a classification draft leaves product metadata and source line unchanged', async () => {
  await render(); await click('تصنيف الخيارات'); await change('نص كرت الإهداء', 'informational');
  await click('إلغاء التصنيف');
  expect(savePolicy).not.toHaveBeenCalled(); expect(onApply).not.toHaveBeenCalled();
  expect(definition.options.find(item => item.id === 'card').inventory_role).toBe('customization');
});

test('unmounting an open editor releases its parent navigation and busy flags', async () => {
  await render(); await click('تصنيف الخيارات');
  expect(onEditingChange).toHaveBeenLastCalledWith(true);
  await act(async () => root.render(null));
  expect(onEditingChange).toHaveBeenLastCalledWith(false);
  expect(onBusyChange).toHaveBeenLastCalledWith(false);
});
