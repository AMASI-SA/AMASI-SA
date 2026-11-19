import React from 'react';
import { ledgerDisplayAmount, minorUnits } from './simpleBalancesModel';

export function Icon({ name, className = '' }) {
  return <svg aria-hidden="true" className={`ico ${className}`}><use href={`/operational-ui/icons.svg#${name}`} /></svg>;
}
export function IconBox({ name, tone = '' }) {
  return <span className={`iconsquare ${tone}`}><Icon name={name} /></span>;
}
export function Heading({ title, subtitle, children }) {
  return <div className="heading"><div><h1>{title}</h1>{subtitle && <p className="subtitle">{subtitle}</p>}</div>
    {children && <div className="top-actions">{children}</div>}</div>;
}
export function formatMinor(units) {
  if (typeof units !== 'bigint') return 'غير متاح';
  const absolute = units < 0n ? -units : units;
  return (units < 0n ? '−' : '') + (absolute / 100n).toLocaleString('en-US') + '.' + String(absolute % 100n).padStart(2, '0');
}
export function Amount({ value, currency = 'SAR' }) {
  const units = minorUnits(value);
  return units === null ? 'غير متاح' : <span className="amt"><bdi>{formatMinor(units)}</bdi> <span className="unit">{currency === 'SAR' ? 'ر.س' : currency}</span></span>;
}
export function AdAmount({ value, currency, payable = false }) {
  const amount = payable ? ledgerDisplayAmount(value, 'ad_account') : value;
  const units = minorUnits(amount);
  const tone = units === null || units === 0n ? undefined : units < 0n ? 'debt' : 'credit';
  return <span data-ad-balance={tone}><Amount value={amount} currency={currency} /></span>;
}
export function AdBalance({ resource }) {
  if (!resource.access?.balance || resource.balance?.visible === false) return null;
  const amounts = resource.ad_amounts;
  if (!amounts) {
    return <>{resource.ad_wallet_balance != null && <span>رصيد مقدم <AdAmount value={resource.ad_wallet_balance} currency={resource.currency} /></span>}
      <small className="under">المبلغ المستحق غير متاح</small></>;
  }
  const prepaid = minorUnits(amounts.prepaid), payable = minorUnits(amounts.payable);
  if (prepaid === null || payable === null) return <span>الرصيد غير متاح</span>;
  const showPrepaid = prepaid !== 0n;
  return <>
    {showPrepaid && <span>رصيد مقدم <AdAmount value={amounts.prepaid} currency={amounts.currency} /></span>}
    {showPrepaid && payable !== 0n && <br />}
    {payable !== 0n && <span>علينا <AdAmount payable value={amounts.payable} currency={amounts.currency} /></span>}
    {!showPrepaid && payable === 0n && <span>الحساب متوازن <Amount value="0.00" currency={amounts.currency} /></span>}
    {amounts.native && <small className="under">{showPrepaid && <>رصيد مقدم <AdAmount value={amounts.native.prepaid} currency="USD" /> </>}
      {payable !== 0n && <>علينا <AdAmount payable value={amounts.native.payable} currency="USD" /> قبل الرسوم</>}</small>}
    {amounts.currency === 'USD' && <small className="under">التحويل إلى الريال غير متاح</small>}
  </>;
}
export const kindStyles = {
  bank:['بنك','landmark','bank'], cash:['صندوق','banknote','cash'], provider:['مزود دفع','credit-card','provider'],
  ad_account:['حساب إعلاني','megaphone','ads'], pos_receivable:['شبكة','credit-card','provider'],
  supplier:['مورد','briefcase-business','party'], employee:['موظف','user-round','party'],
  employee_custody:['عهدة','hand-coins','party'], store_driver:['موصل','truck','party'], courier:['شركة شحن','truck','party'],
  external_person:['جهة خارجية','building-2','party'], owner_withdrawal:['حساب مالك','wallet','party'],
  operating_expense:['مصروف','receipt-text','party'],
};
export const assetGroups = [['البنوك',['bank']],['الصناديق والمحافظ',['cash']],['منصات الدفع',['provider','pos_receivable']],['حسابات الإعلانات',['ad_account']]];
export const partyGroups = [['الموردون',['supplier']],['الموظفون والعهد',['employee','employee_custody']],['الشحن والموصلون',['courier','store_driver']],['الجهات الخارجية',['external_person']],['حسابات المالك والمصروفات',['owner_withdrawal','operating_expense']]];
export const isAsset = kind => assetGroups.some(([, kinds]) => kinds.includes(kind));
export const styleOf = r => r.prepaid_shipping_cash ? ['رصيد شحن مقدم','package-check','cash'] : kindStyles[r.kind] || ['جهة','building-2','party'];
export function searchable(value) {
  return String(value || '').normalize('NFKC').replace(/[\u064B-\u065F\u0670\u0640]/g, '').replace(/[أإآٱ]/g,'ا').replace(/ى/g,'ي').replace(/[٠-٩]/g,d=>String('٠١٢٣٤٥٦٧٨٩'.indexOf(d))).trim().toLowerCase();
}
export function issueLabel(n) {
  if (n == null) return 'عرض الطلبات التي تحتاج مراجعة';
  if (n === 1) return 'طلب واحد يحتاج مراجعة';
  if (n === 2) return 'طلبان يحتاجان مراجعة';
  return n + (n >= 3 && n <= 10 ? ' طلبات تحتاج مراجعة' : ' طلبًا يحتاج مراجعة');
}


export function MovementIcon({ type }) {
  const appearance = {
    in: ['arrow-down-left','c-in'], out: ['arrow-up-right','c-out'], transfer: ['arrow-left-right','c-move'],
  }[type] || ['receipt-text','provider'];
  return <IconBox name={appearance[0]} tone={appearance[1]} />;
}
