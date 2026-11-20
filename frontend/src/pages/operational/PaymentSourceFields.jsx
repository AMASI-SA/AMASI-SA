import React from 'react';
import { useBalanceRead } from './simpleBalancesHooks';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { minorUnits, resourceKey, signedResourceBalance } from './simpleBalancesModel';

const fundingKinds = { bank: 'بنك', cash: 'صندوق نقدي', employee_custody: 'عهدة موظف' };
export const insufficientSource = 'رصيد مصدر الدفع لا يكفي. قلّل المبلغ أو اختر مصدرًا آخر.';

export function sourceAvailability(item) {
  if (typeof item?.can_fund_payment === 'boolean') return item.can_fund_payment;
  const balance = minorUnits(signedResourceBalance(item));
  return balance !== null ? balance > 0n : item?.kind === 'employee_custody' ? false : null;
}

export function exceedsSource(item, amount) {
  const available = minorUnits(signedResourceBalance(item)), requested = minorUnits(amount);
  return sourceAvailability(item) === false || (available !== null && requested !== null && requested > available);
}

export function usePaymentSourceGroup(context, candidates, kind, enabled) {
  const onDemand = enabled && context.directoryOnly === true;
  const eligible = onDemand && Boolean(kind) && candidates.some(item => item.kind === kind && item.access?.use);
  const binding = JSON.stringify([context.actor.id, context.session_scope, context.permission_version, kind]);
  const read = useBalanceRead(async config => {
    const result = await api.accountGroup(kind, config);
    if (result?.session_scope !== context.session_scope || result.permission_version !== context.permission_version
      || !Array.isArray(result.items) || result.items.some(item => item.kind !== kind || !item.id || !item.access)) {
      throw {localMessage:'تعذر التحقق من صلاحيات مصدر الدفع. حدّث القائمة ثم أعد المحاولة.'};
    }
    return result.items;
  }, binding, eligible);
  const allowed = new Set(candidates.filter(item => item.access?.use).map(resourceKey));
  const rows = onDemand ? (read.data || []).filter(item => allowed.has(resourceKey(item)) && item.access?.use && (item.currency || 'SAR') === 'SAR'
    && !item.prepaid_shipping_cash && (item.kind !== 'employee_custody' || sourceAvailability(item) === true)) : candidates;
  return { rows, onDemand, loading: eligible && !read.data && !read.error, error: read.error, reload: read.reload };
}

export default function PaymentSourceFields({ sources, group, kind, value, onKind, onSource, disabled, label, fieldClass = 'field' }) {
  const eligible = sources.filter(item => item.access?.use && !item.prepaid_shipping_cash
    && (item.kind !== 'employee_custody' || group?.onDemand || sourceAvailability(item) === true));
  const kinds = Object.keys(fundingKinds).filter(key => eligible.some(item => item.kind === key));
  const selectedKind = kinds.includes(kind) ? kind : '';
  const choices = (group?.onDemand ? group.rows : eligible).filter(item => item.kind === selectedKind);
  return <>
    <label className={fieldClass}><span>نوع مصدر الدفع</span><select value={selectedKind} disabled={disabled} required onChange={e => onKind(e.target.value)}>
      <option value="">اختر نوع المصدر</option>{kinds.map(key => <option key={key} value={key}>{fundingKinds[key]}</option>)}
    </select></label>
    {group?.loading && <p role="status" className="note">جارٍ تحميل حسابات مصدر الدفع…</p>}
    {group?.error && <p role="alert" className="note warning">{group.error} <button type="button" onClick={group.reload}>إعادة المحاولة</button></p>}
    {selectedKind && !group?.loading && !group?.error && choices.length === 0 && <p role="status" className="note">{selectedKind === 'employee_custody' ? 'لا توجد عهدة ممولة متاحة للصرف.' : 'لا يوجد حساب متاح من هذا النوع.'}</p>}
    {selectedKind && !group?.loading && !group?.error && choices.length > 0 && <label className={fieldClass}><span>{label}</span><select value={value} disabled={disabled} required onChange={e => onSource(e.target.value)}>
      <option value="">اختر الحساب</option>{choices.map(item => <option key={resourceKey(item)} value={resourceKey(item)} disabled={group?.onDemand ? sourceAvailability(item) !== true : sourceAvailability(item) === false}>
        {item.name}{(group?.onDemand ? sourceAvailability(item) !== true : sourceAvailability(item) === false) ? ' — غير متاح للصرف' : ''}
      </option>)}
    </select></label>}
    {!kinds.length && <p className="note" role="status">لا يوجد مصدر دفع متاح لك لهذه الجهة.</p>}
  </>;
}
