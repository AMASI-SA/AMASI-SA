// Display identity only. These aliases never select accounts or change accounting.
export function normalizeBrandAlias(value) {
  return typeof value === 'string' ? value.normalize('NFKC')
    .replace(/[\u064B-\u065F\u0670\u0640]/g, '').replace(/[أإآٱ]/g, 'ا')
    .replace(/ى/g, 'ي').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase() : '';
}

const picture = (name, file = name) => ({ name, src: `/operational-ui/brands/${file}.png` });
const badge = (name, text = name) => ({ name, text });
export const BRANDS = Object.freeze({
  salla: picture('سلة', 'salla'), tabby: picture('تابي', 'tabby'), tamara: picture('تمارا', 'tamara'),
  paypal: picture('باي بال', 'paypal'), emkan: badge('إمكان'),
  alinma: picture('الإنماء', 'enma'), alrajhi: picture('الراجحي', 'rajhi'), snb: badge('الأهلي السعودي', 'SNB'),
  riyad: badge('بنك الرياض', 'الرياض'), albilad: badge('بنك البلاد', 'البلاد'),
  aljazira: badge('بنك الجزيرة', 'الجزيرة'), anb: badge('البنك العربي الوطني', 'anb'),
  sab: badge('البنك السعودي الأول', 'SAB'), bsfr: badge('البنك السعودي الفرنسي', 'BSF'),
  saib: badge('البنك السعودي للاستثمار', 'SAIB'),
  meta: picture('Meta', 'meta'), snapchat: picture('Snapchat', 'snap'),
  google: picture('Google Ads', 'google'), tiktok: picture('TikTok', 'tiktok'),
  apple: picture('Apple Pay', 'apple'), visa: picture('Visa', 'visa'),
  mastercard: picture('Mastercard', 'mastercard'), mada: badge('مدى'), stcpay: badge('STC Pay', 'stc pay'),
  aramex: picture('أرامكس', 'aramex'), smsa: badge('سمسا', 'SMSA'),
  spl: badge('البريد السعودي', 'SPL'), dhl: badge('DHL'), fedex: badge('FedEx'),
  imile: badge('iMile'), jtexpress: badge('J&T Express', 'J&T'),
});

const aliases = entries => Object.freeze(Object.fromEntries(Object.entries(entries).flatMap(([key, values]) =>
  [key, ...values].map(value => [normalizeBrandAlias(value), key]))));
const payment = aliases({
  salla: ['سلة', 'سله', 'salla pay', 'سلة باي'], tabby: ['تابي', 'tabby installment', 'TabbyInstallment'],
  tamara: ['تمارا', 'tamara installment', 'TamaraInstallment'],
  emkan: ['إمكان', 'امكان', 'emkan installment', 'EmkanInstallment', 'emkan pay'], paypal: ['باي بال', 'بايبال', 'pay pal'],
  mada: ['مدى'], visa: ['فيزا'], mastercard: ['master card', 'ماستر كارد', 'ماستركارد'],
  apple: ['apple pay', 'ابل باي', 'آبل باي'], stcpay: ['stc pay', 'اس تي سي باي'],
});
const banks = aliases({
  alinma: ['enma', 'alinma bank', 'الإنماء', 'بنك الإنماء', 'مصرف الإنماء'],
  alrajhi: ['rajhi', 'al rajhi', 'al rajhi bank', 'alrajhi bank', 'الراجحي', 'بنك الراجحي', 'مصرف الراجحي'],
  snb: ['ahli', 'al ahli', 'saudi national bank', 'الأهلي', 'بنك الأهلي', 'البنك الأهلي', 'البنك الأهلي السعودي', 'الأهلي السعودي'],
  riyad: ['riyad bank', 'بنك الرياض'], albilad: ['bank albilad', 'بنك البلاد'],
  aljazira: ['bank aljazira', 'بنك الجزيرة'], anb: ['arab national bank', 'البنك العربي الوطني'],
  sab: ['saudi awwal bank', 'البنك السعودي الأول'], bsfr: ['banque saudi fransi', 'البنك السعودي الفرنسي'],
  saib: ['saudi investment bank', 'البنك السعودي للاستثمار'],
});
const ads = aliases({
  meta: ['facebook', 'facebook ads', 'meta ads', 'ميتا', 'فيسبوك'],
  snapchat: ['snap', 'snapchat ads', 'سناب', 'سناب شات', 'سنابشات', 'حساب سناب'],
  google: ['google ads', 'google_ads', 'جوجل', 'جوجل ادز'],
  tiktok: ['tik tok', 'tiktok ads', 'تيك توك', 'تيكتوك'],
});
const carriers = aliases({
  aramex: ['أرامكس', 'ارامكس'], smsa: ['smsa express', 'سمسا'],
  spl: ['saudi post', 'البريد السعودي', 'سبل'], dhl: ['دي اتش ال'],
  fedex: ['fed ex', 'فيديكس'], imile: ['i mile', 'اي مايل', 'آي مايل'],
  jtexpress: ['j&t', 'j&t express', 'جي اند تي'],
});

const resolve = (map, value) => {
  const key = normalizeBrandAlias(value);
  return Object.hasOwn(map, key) ? map[key] : null;
};

export function brandForResource(resource) {
  if (!resource || resource.prepaid_shipping_cash || resource.kind === 'cash') return null;
  const maps = { provider: payment, pos_receivable: payment, bank: banks, ad_account: ads, courier: carriers };
  if (!Object.hasOwn(maps, resource.kind)) return null;
  const map = maps[resource.kind];
  // Explicit metadata wins, including an unrecognized value: do not guess from a renamed account.
  const metadata = resource.kind === 'ad_account' ? resource.platform : resource.kind === 'bank' ? resource.bank_code : null;
  if (typeof metadata === 'string' && metadata.trim()) return resolve(map, metadata);
  const identity = resolve(map, resource.id);
  if (identity) return identity;
  const name = resolve(map, resource.name);
  if (name) return name;
  // The existing ad source serializes its bound platform as "platform: display_name".
  // Match that complete prefix only, never substrings in user-supplied display names.
  if (resource.kind === 'ad_account' && typeof resource.name === 'string' && resource.name.includes(':')) {
    return resolve(map, resource.name.slice(0, resource.name.indexOf(':')));
  }
  return null;
}

// Financial-position rows use a typed "kind:identity:currency" display key.
// Decode only that contract; in the mixed bank/POS table a name cannot decide the kind.
export function positionRowResource(tableId, row) {
  const serialized = typeof row?.id === 'string'
    ? /^(bank|pos_receivable|cash|provider|courier|store_driver|ad_account):(.+):[A-Z]{3}(?::(?:receivable|payable))?$/.exec(row.id) : null;
  const knownTables = {providers:'provider',couriers:'courier',drivers:'store_driver',cash:'cash',
    ads:'ad_account',ads_currency_conversion:'ad_account',provider_collections_expected:'provider'};
  const kind = tableId === 'banks_network'
    ? (['bank','pos_receivable'].includes(serialized?.[1]) ? serialized[1] : null)
    : tableId === 'balance_origin'
      ? (['provider','courier','store_driver'].includes(serialized?.[1]) ? serialized[1] : null)
      : (Object.hasOwn(knownTables, tableId) ? knownTables[tableId] : null);
  if (!kind) return null;
  return {kind, id: kind === 'provider' && row.provider ? row.provider
    : serialized?.[1] === kind ? serialized[2] : row.id, name:row.name, platform:row.platform};
}
