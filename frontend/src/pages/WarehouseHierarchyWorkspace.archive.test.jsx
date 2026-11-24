import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import WarehouseHierarchyWorkspace from './WarehouseHierarchyWorkspace';
import api from '../lib/api';

jest.mock('../lib/api', () => ({__esModule: true, default: {get: jest.fn(), post: jest.fn()}}));
jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn(), warning: jest.fn()}}));
jest.mock('../components/inventory/suite/InventorySuiteFrame', () => ({
  __esModule: true,
  default: ({children}) => <div>{children}</div>,
  InventoryPageHeader: ({title, children}) => <header><h1>{title}</h1>{children}</header>,
  InventorySheet: ({open, title, description, children, footer, onClose, busy}) => open ? (
    <section role="dialog" aria-label={title}>
      <h2>{title}</h2><p>{description}</p>
      <button type="button" aria-label="إغلاق" disabled={busy} onClick={onClose}>إغلاق</button>
      {children}{footer}
    </section>
  ) : null,
}));

const oldCabinet = {id: 'cab-old', name: 'دولاب المراجعة', cabinet_number: 1, code: 'S01-C01', warehouse_id: 'wh-1', room_id: 'sec-1', length: 3, width: 3, total_locations: 9, purpose: 'temporary_staging', location_numbering: 'legacy'};
const newCabinet = {id: 'cab-new', name: 'دولاب اماسي الافتراضي', cabinet_number: 2, code: 'S01-C02', warehouse_id: 'wh-1', room_id: 'sec-1', length: 1, width: 6, total_locations: 6, purpose: 'permanent_storage', location_numbering: 'cabinet-column-row'};
const otherCabinet = {...newCabinet, id: 'cab-other', name: 'دولاب جدة', warehouse_id: 'wh-2', room_id: 'sec-2'};
let host, root, activeCabinets, archivedCabinets;
const clone = value => JSON.parse(JSON.stringify(value));
const button = (text, scope = host) => [...scope.querySelectorAll('button')].find(node => node.textContent === text);
const dialog = () => host.querySelector('[role="dialog"]');
async function click(node) { await act(async () => node.click()); }
async function render(mode = null) { await act(async () => root.render(<WarehouseHierarchyWorkspace accessMode={mode}/>)); }
async function submitArchive() { await act(async () => host.querySelector('#warehouse-archive-form').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
function sectionsFor(warehouseId) {
  const roomId = warehouseId === 'wh-1' ? 'sec-1' : 'sec-2';
  const cabinets = activeCabinets.filter(row => row.warehouse_id === warehouseId);
  return [{id: roomId, warehouse_id: warehouseId, name: warehouseId === 'wh-1' ? 'الاستلام' : 'تخزين جدة', capabilities: ['cabinets'], cabinet_count: cabinets.length, cabinets: clone(cabinets)}];
}
function locationsFor(id) {
  const cabinet = [oldCabinet, newCabinet, otherCabinet].find(row => row.id === id);
  return Array.from({length: cabinet?.total_locations || 0}, (_, index) => ({
    id: `${id}-slot-${index + 1}`, number: index + 1, row: Math.floor(index / cabinet.width) + 1,
    column: index % cabinet.width + 1, code: `${cabinet.code}-${String(index + 1).padStart(3, '0')}`,
    state: 'empty', occupancy: null,
  }));
}

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  activeCabinets = clone([oldCabinet, newCabinet, otherCabinet]); archivedCabinets = [];
  api.get.mockReset(); api.post.mockReset();
  api.get.mockImplementation(async (path, options) => {
    if (path === '/warehouse-locations-v2/catalog') return {data: {default_country: 'السعودية', default_city: 'الرياض', countries: [{name: 'السعودية', cities: ['الرياض', 'جدة']}]}};
    if (path === '/warehouse-locations/warehouses') return {data: {items: [
      {id: 'wh-1', name: 'بيت العود', city: 'الرياض', code: 'WH01'},
      {id: 'wh-2', name: 'فرع جدة', city: 'جدة', code: 'WH02'},
    ]}};
    if (path === '/warehouse-locations/locations') return {data: {items: locationsFor(options.params.cabinet_id)}};
    if (path.endsWith('/sections')) return {data: {items: sectionsFor(path.split('/')[3])}};
    if (path.endsWith('/archived-cabinets')) return {data: {items: clone(archivedCabinets.filter(row => row.warehouse_id === path.split('/')[3])), total: archivedCabinets.length}};
    return {data: {}};
  });
  api.post.mockImplementation(async path => {
    const id = path.split('/')[3];
    if (path.endsWith('/archive')) {
      const cabinet = activeCabinets.find(row => row.id === id);
      activeCabinets = activeCabinets.filter(row => row.id !== id);
      archivedCabinets.push({...cabinet, status: 'archived'});
      return {data: {ok: true, cabinet: {...cabinet, status: 'archived'}}};
    }
    if (path.endsWith('/restore')) {
      const cabinet = archivedCabinets.find(row => row.id === id);
      archivedCabinets = archivedCabinets.filter(row => row.id !== id);
      activeCabinets.push({...cabinet, status: 'active'});
      return {data: {ok: true, cabinet: {...cabinet, status: 'active'}}};
    }
    throw new Error(`Unexpected write: ${path}`);
  });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.clearAllMocks(); });

test('archive confirmation names the exact cabinet and cancellation makes no write', async () => {
  await render();
  expect(button('أرشفة الدولاب')).toBeDefined();
  await click(button('أرشفة الدولاب'));
  expect(dialog().textContent).toContain('دولاب المراجعة');
  expect(dialog().textContent).toContain('د1');
  expect(dialog().textContent).toContain('9');
  expect(dialog().textContent).toContain('استعادته');
  await click(dialog().querySelector('[aria-label="إغلاق"]'));
  expect(dialog()).toBeNull();
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(9);
  expect(api.post).not.toHaveBeenCalled();
});

test('archive uses the confirmed target and preserves the new cabinet and its six short codes', async () => {
  await render(); await click(button('أرشفة الدولاب'));
  // The confirmation owns its target even if a background selection changes.
  await click([...host.querySelectorAll('.mz-wh-cabinet')].find(node => node.textContent.includes('د2')));
  expect(dialog().textContent).toContain('دولاب المراجعة');
  await submitArchive();
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith('/warehouse-locations-v2/cabinets/cab-old/archive');
  expect(dialog()).toBeNull();
  expect([...host.querySelectorAll('.mz-wh-bin strong')].map(node => node.textContent)).toEqual(['211', '221', '231', '241', '251', '261']);
  expect(host.querySelector('.mz-wh-cabinet-header').textContent).toContain(newCabinet.name);
  expect(activeCabinets.find(row => row.id === 'cab-new')).toEqual(newCabinet);
  expect(api.get).toHaveBeenCalledWith('/warehouse-locations-v2/warehouses/wh-1/archived-cabinets');
});

test('archive rejection keeps the target visible and explains the blocker without changing selection', async () => {
  api.post.mockRejectedValue({response: {data: {detail: {message: 'الدولاب مرتبط بمخزون؛ أفرغ خاناته أولًا.'}}}});
  await render(); await click(button('أرشفة الدولاب')); await submitArchive();
  expect(dialog().querySelector('[role="alert"]').textContent).toContain('مرتبط بمخزون');
  expect(dialog().textContent).toContain(oldCabinet.name);
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(9);
  expect(activeCabinets).toEqual([oldCabinet, newCabinet, otherCabinet]);
});

test('duplicate archive submissions are blocked while the first request is pending', async () => {
  const pending = deferred(); api.post.mockImplementation(() => pending.promise);
  await render(); await click(button('أرشفة الدولاب'));
  await submitArchive(); await submitArchive();
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(dialog().querySelector('[aria-label="إغلاق"]').disabled).toBe(true);
  await act(async () => pending.reject({response: {data: {detail: {message: 'تعذر الإكمال'}}}}));
  expect(dialog().querySelector('[role="alert"]').textContent).toContain('تعذر الإكمال');
});

test('archived cabinets remain reachable when the last active cabinet is archived and can be restored', async () => {
  activeCabinets = [clone(oldCabinet), clone(otherCabinet)];
  await render(); await click(button('أرشفة الدولاب')); await submitArchive();
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(0);
  expect(button('الدواليب المؤرشفة').disabled).toBe(false);
  await click(button('الدواليب المؤرشفة'));
  expect(dialog().textContent).toContain(oldCabinet.name);
  await click(button('استعادة', dialog()));
  expect(api.post).toHaveBeenLastCalledWith('/warehouse-locations-v2/cabinets/cab-old/restore');
  expect(dialog()).toBeNull();
  expect(host.querySelector('.mz-wh-cabinet-header').textContent).toContain(oldCabinet.name);
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(9);
});

test('restore rejection remains in the archived panel with the server explanation', async () => {
  activeCabinets = [clone(newCabinet), clone(otherCabinet)]; archivedCabinets = [{...oldCabinet, status: 'archived'}];
  api.post.mockRejectedValue({response: {data: {detail: {message: 'أعد تفعيل القسم قبل استعادة الدولاب.'}}}});
  await render(); await click(button('الدواليب المؤرشفة')); await click(button('استعادة', dialog()));
  expect(dialog().querySelector('[role="alert"]').textContent).toContain('أعد تفعيل القسم');
  expect(dialog().textContent).toContain(oldCabinet.name);
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(6);
});

test.each(['read', 'write'])('delegated access %s does not expose lifecycle actions or read the archive', async mode => {
  await render(mode);
  expect(button('أرشفة الدولاب')).toBeUndefined();
  expect(button('الدواليب المؤرشفة')).toBeUndefined();
  expect(api.get.mock.calls.some(([path]) => path.endsWith('/archived-cabinets'))).toBe(false);
  expect(api.post).not.toHaveBeenCalled();
});

test('archive list reports loading, errors, retry and an empty result', async () => {
  const original = api.get.getMockImplementation(), pending = deferred();
  api.get.mockImplementation((path, ...args) => path.endsWith('/archived-cabinets') ? pending.promise : original(path, ...args));
  await render(); await click(button('الدواليب المؤرشفة'));
  expect(dialog().querySelector('[role="status"]').textContent).toContain('تحميل');
  await act(async () => pending.reject(new Error('offline')));
  expect(dialog().querySelector('[role="alert"]').textContent).toContain('تعذر');
  api.get.mockImplementation(original); await click(button('إعادة المحاولة', dialog()));
  expect(dialog().textContent).toContain('لا توجد دواليب مؤرشفة');
  expect(api.post).not.toHaveBeenCalled();
});

test('late archive response from another branch cannot replace the current archive list', async () => {
  const original = api.get.getMockImplementation(), pending = deferred();
  api.get.mockImplementation((path, ...args) => path === '/warehouse-locations-v2/warehouses/wh-1/archived-cabinets' ? pending.promise : original(path, ...args));
  await render(); await click(button('الدواليب المؤرشفة'));
  await click([...host.querySelectorAll('.mz-wh-branch')].find(node => node.textContent.includes('فرع جدة')));
  expect(dialog()).toBeNull();
  await click(button('الدواليب المؤرشفة'));
  expect(dialog().textContent).toContain('فرع جدة');
  await act(async () => pending.resolve({data: {items: [{...oldCabinet, status: 'archived'}]}}));
  expect(dialog().textContent).not.toContain(oldCabinet.name);
  expect(dialog().textContent).toContain('لا توجد دواليب مؤرشفة');
  expect(api.post).not.toHaveBeenCalled();
});

test('a refresh failure after successful archive clears the old cabinet and reports the saved change accurately', async () => {
  const originalGet = api.get.getMockImplementation(), originalPost = api.post.getMockImplementation();
  api.post.mockImplementation(async path => {
    const result = await originalPost(path);
    api.get.mockImplementation((url, ...args) => url.endsWith('/sections') ? Promise.reject(new Error('read offline')) : originalGet(url, ...args));
    return result;
  });
  await render(); await click(button('أرشفة الدولاب')); await submitArchive();
  expect(dialog()).toBeNull();
  expect(host.querySelector('[role="alert"]').textContent).toContain('تم حفظ التغيير');
  expect(button('أرشفة الدولاب')).toBeUndefined();
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(0);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(archivedCabinets.map(row => row.id)).toEqual(['cab-old']);
  api.get.mockImplementation(originalGet); await click(button('إعادة المحاولة'));
  expect([...host.querySelectorAll('.mz-wh-bin strong')].map(node => node.textContent)).toEqual(['211', '221', '231', '241', '251', '261']);
  expect(api.post).toHaveBeenCalledTimes(1);
});

test('restoration selects the archived cabinet in its own section, not the currently selected section', async () => {
  const restored = {...oldCabinet, room_id: 'sec-archived', section_id: 'sec-archived'};
  activeCabinets = [clone(newCabinet), clone(otherCabinet)]; archivedCabinets = [{...restored, status: 'archived'}];
  const originalGet = api.get.getMockImplementation();
  api.get.mockImplementation((path, ...args) => path === '/warehouse-locations-v2/warehouses/wh-1/sections' ? Promise.resolve({data: {items: [
    {...sectionsFor('wh-1')[0], cabinets: clone(activeCabinets.filter(row => row.warehouse_id === 'wh-1' && row.room_id === 'sec-1'))},
    {id: 'sec-archived', name: 'قسم آخر', warehouse_id: 'wh-1', capabilities: ['cabinets'], cabinets: clone(activeCabinets.filter(row => row.room_id === 'sec-archived'))},
  ]}}) : originalGet(path, ...args));
  await render(); await click(button('الدواليب المؤرشفة')); await click(button('استعادة', dialog()));
  expect(host.querySelector('.mz-wh-breadcrumb').textContent).toContain('قسم آخر');
  expect(host.querySelector('.mz-wh-cabinet-header').textContent).toContain(oldCabinet.name);
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(9);
});
