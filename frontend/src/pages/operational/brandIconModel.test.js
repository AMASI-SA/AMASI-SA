import { BRANDS, brandForResource, normalizeBrandAlias, positionRowResource } from './brandIconModel';

test.each([
  ['provider', ' EMKAN ', 'unknown', 'emkan'], ['provider', 'emkan_installment', 'unknown', 'emkan'],
  ['provider', 'EmkanInstallment', 'unknown', 'emkan'], ['provider', 'TaBbYiNsTaLlMeNt', 'unknown', 'tabby'],
  ['provider', 'TamaraInstallment', 'unknown', 'tamara'],
  ['provider', 'id', 'إمْكَان', 'emkan'], ['provider', 'id', 'باي بال', 'paypal'],
  ['provider', 'tabby', 'الحساب الخاص', 'tabby'], ['provider', 'salla', 'سلة', 'salla'],
  ['bank', 'id', 'بنك الإنماء', 'alinma'], ['bank', 'id', 'مصرف الراجحي', 'alrajhi'],
  ['bank', 'id', 'البنك الأهلي السعودي', 'snb'], ['bank', 'id', 'بنك الرياض', 'riyad'],
  ['ad_account', 'id', 'meta: حساب المتجر', 'meta'], ['ad_account', 'id', 'google_ads: المتجر', 'google'],
  ['ad_account', 'id', 'snapchat: متجر تمارا', 'snapchat'], ['ad_account', 'id', 'TikTok', 'tiktok'],
  ['courier', 'aramex', 'خدمة الشحن', 'aramex'], ['courier', 'id', 'أرامكس', 'aramex'],
  ['courier', 'id', 'سمسا', 'smsa'], ['pos_receivable', 'id', 'مدى', 'mada'],
])('recognizes an exact %s identity without changing it (%s / %s)', (kind, id, name, expected) => {
  const resource = Object.freeze({kind, id, name, balance: '42.00'});
  expect(brandForResource(resource)).toBe(expected);
  expect(resource.balance).toBe('42.00');
});

test.each([
  {kind:'bank',name:'مورد الراجحي'}, {kind:'bank',name:'بنك الراجحي الجديد'},
  {kind:'supplier',name:'بنك الراجحي'}, {kind:'external_person',id:'paypal',name:'باي بال'},
  {kind:'cash',name:'سلة'}, {kind:'cash',id:'alrajhi',name:'بنك الراجحي'},
  {kind:'cash',name:'محفظة سلة',prepaid_shipping_cash:true},
  {kind:'courier',id:'aramex',prepaid_shipping_cash:true},
  {kind:'provider',id:'not_emkan_test',name:'Emkan Store'},
  {kind:'provider',id:'NotEmkanInstallment',name:'unknown'},
  {kind:'ad_account',name:'مكتب meta الإعلاني'}, {kind:'ad_account',name:'other: meta'},
  {kind:'ad_account',name:'google ads: حساب',platform:'unknown'},
  {kind:'bank',name:'الراجحي',bank_code:'unknown'},
  {kind:'provider',id:'constructor',name:'toString'}, {kind:'constructor',name:'name'},
  {kind:'bank',name:'https://example.test/rajhi.png'}, {kind:'provider',id:{},name:[]},
  null, {},
])('unknown, conflicting-kind and prepaid identities stay generic: %j', resource => {
  expect(brandForResource(resource)).toBeNull();
});

test('explicit platform metadata identifies renamed ad accounts without guessing from their name', () => {
  expect(brandForResource({kind:'ad_account',name:'حساب المتجر الرئيسي',platform:'snapchat'})).toBe('snapchat');
  expect(brandForResource({kind:'ad_account',name:'Meta',platform:'tiktok'})).toBe('tiktok');
  expect(normalizeBrandAlias('  إِمْكَان_installment  ')).toBe('امكان installment');
});

test('every image is a fixed local asset and no arbitrary resource value can become its path', () => {
  Object.values(BRANDS).forEach(brand => {
    if (brand.src) expect(brand.src).toMatch(/^\/operational-ui\/brands\/[a-z]+\.png$/);
    else expect(typeof brand.text).toBe('string');
  });
});

test('financial table descriptors retain typed identities and never reclassify POS or cash by bank name', () => {
  const bank=positionRowResource('banks_network',{id:'bank:account:b1:SAR',name:'بنك الراجحي'});
  expect(bank).toMatchObject({kind:'bank',id:'account:b1'});
  expect(brandForResource(bank)).toBe('alrajhi');
  const pos=positionRowResource('banks_network',{id:'pos_receivable:terminal:SAR',name:'بنك الراجحي'});
  expect(pos.kind).toBe('pos_receivable');
  expect(brandForResource(pos)).toBeNull();
  expect(positionRowResource('banks_network',{id:'untyped',name:'بنك الراجحي'})).toBeNull();
  expect(brandForResource(positionRowResource('cash',{id:'cash:wallet:SAR',name:'سلة'}))).toBeNull();
  expect(brandForResource(positionRowResource('balance_origin',{id:'provider:tabby:SAR:receivable',name:'تابي'}))).toBe('tabby');
  expect(brandForResource(positionRowResource('ads',{id:'ad_account:a:SAR',name:'snapchat: حساب المتجر'}))).toBe('snapchat');
  expect(positionRowResource('products_unreceived',{id:'p1',name:'سلة'})).toBeNull();
});
