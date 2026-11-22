import React, { lazy, Suspense, useEffect, useState } from 'react';
import { Amount, Heading, Icon, IconBox } from './ApprovedFinanceUI';
import { todaySA } from '../../lib/dates';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { pendingBalanceStorageKey, usePendingBalanceCommand } from './simpleBalancesPending';
import { decimalAmount, entryChoices, isMoneyAccount, minorUnits, permittedOperations, resourceKey, resourceLabels } from './simpleBalancesModel';

import PendingEntryActions from './PendingEntryActions';
import PaymentSourceFields, { exceedsSource, insufficientSource, sourceAvailability, usePaymentSourceGroup } from './PaymentSourceFields';
import WorkspaceResourcePicker from './WorkspaceResourcePicker';

const RefundScreen = lazy(() => import('./ApprovedRefund'));
export function ApprovedRefund(props) {
  return <Suspense fallback={<p role="status">جارٍ فتح إرجاع المبلغ…</p>}><RefundScreen {...props} /></Suspense>;
}

const modes = {
  in: ['استلمت مبلغ', 'arrow-down-left', 'c-in'], out: ['دفعت مبلغ', 'arrow-up-right', 'c-out'],
  move: ['حوّلت بين حسابين', 'arrow-left-right', 'c-move'], ads: ['شحن رصيد إعلان', 'arrow-up-right', 'c-out'],
  wallet: ['شحن صندوق سلة', 'arrow-left-right', 'c-move'],
};
const entryTitle = (mode, resource) => resource?.kind === 'courier' && ['in', 'out'].includes(mode)
  ? `${mode === 'in' ? 'استلام تحصيل من' : 'سداد أجور'} ${resource.name}` : modes[mode][0];
const OriginalEntry = lazy(() => import('./SimpleBalances').then(module => ({ default: module.EntryForm })));
const cash = resource => resource && isMoneyAccount(resource.kind);
const transfer = mode => mode === 'move' || mode === 'wallet';
const side = mode => mode === 'in' ? 'for_party' : 'for_us';
function reasonFor(resource, mode) {
  if (transfer(mode)) return 'transfer';
  if (resource.kind === 'external_person') return mode === 'in' ? 'receipt' : '';
  return mode === 'in' ? 'collection' : 'payment';
}
function canTarget(context, resource, mode) {
  if (!resource || resource.access?.use !== true) return false;
  if (transfer(mode) ? !cash(resource) : cash(resource)) return false;
  if (mode === 'ads' && resource.kind !== 'ad_account') return false;
  if (mode === 'wallet' && resource.prepaid_shipping_cash !== true) return false;
  if ((resource.currency || 'SAR') !== 'SAR' && !resource.ad_bank_funding) return false;
  return entryChoices(resource.kind, side(mode), permittedOperations(context, resource)).some(item => item.reason === reasonFor(resource, mode));
}
const isPayPal = r => r?.kind === "provider" && r.id === "paypal";
function sourcesFor(context, target, mode) {
  return context.resources.filter(item => {
    if (!item.access?.use || (item.currency || 'SAR') !== 'SAR' || target && resourceKey(item) === resourceKey(target)) return false;
    if (target?.ad_bank_funding || isPayPal(target) || target?.kind === 'pos_receivable') return item.kind === 'bank';
    if (cash(item)) return mode !== 'out' || !item.prepaid_shipping_cash;
    return mode === 'out' && ['supplier', 'external_person', 'operating_expense'].includes(target?.kind)
      && item.kind === 'employee_custody' && (context.directoryOnly || sourceAvailability(item) === true) && permittedOperations(context).has('custody.spend');
  });
}
const options = list => [<option key="" value="">اختر من القائمة</option>, ...list.map(r => <option key={resourceKey(r)} value={resourceKey(r)}>{r.name}</option>)];
const field = (label, control) => <label className="field"><span>{label}</span>{control}</label>;
const baseForm = () => ({ amount: '', reference: '', note: '', date: todaySA(), unit: 'receipt', usd: '', fee: '', receipt: '', arrival: false, wallet: false });

export default function ApprovedEntry(props) {
  return <EntryWorkflow key={JSON.stringify([props.context.actor.id, props.context.session_scope,
    props.context.permission_version, props.mode, props.resource?.kind, props.resource?.id])} {...props} />;
}

function EntryWorkflow({ context, resource, mode = 'choose', onBack, onSaved, onPurchase, onRefund }) {
  const [selected, setSelected] = useState(modes[mode] ? mode : 'choose');
  const resourceId = resource && resourceKey(resource);
  const allowedTarget = resource && (context.demandOnly ? resource : context.resources.find(item => resourceKey(item) === resourceId));
  const carrierContext = allowedTarget?.kind === 'courier';
  const granted = permittedOperations(context);
  const sources = context.resources.filter(item => item.access?.use === true && (item.currency || 'SAR') === 'SAR');
  const moneySources = sources.filter(cash).slice(0, 2);
  const bankSource = sources.some(r => r.kind === 'bank');
  const custodySource = sources.some(r => r.kind === 'employee_custody' && (context.directoryOnly || sourceAvailability(r) === true)) && granted.has('custody.spend');
  const canFund = (r, key) => {
    if (r.ad_bank_funding || isPayPal(r) || r.kind === 'pos_receivable') return bankSource;
    return moneySources.some(item => resourceKey(item) !== resourceKey(r))
      || key === 'out' && custodySource && ['supplier', 'external_person', 'operating_expense'].includes(r.kind);
  };
  function movementChoice(key) {
    const [label, icon, tone] = modes[key];
    const enabled = context.status === 'active' && (context.demandOnly
      ? context.capabilities?.create_entries === true && (!carrierContext || canTarget(context, allowedTarget, key))
      : (carrierContext ? [allowedTarget] : context.resources).some(r => canTarget(context, r, key) && canFund(r, key)));
    return <button type="button" className="quickbtn" disabled={!enabled} onClick={() => setSelected(key)}>
      <IconBox name={icon} tone={tone}/>{carrierContext ? entryTitle(key, allowedTarget) : label}<span className="next"><Icon name="chevron-left" className="smallicon"/></span>
    </button>;
  }
  if ((resource && !allowedTarget?.access?.use) || (selected === 'out' && allowedTarget && !cash(allowedTarget) && !canTarget(context, allowedTarget, 'out'))) {
    return <p role="alert" className="note warning">السداد لهذا الحساب غير متاح ضمن صلاحياتك الحالية.</p>;
  }
  if (selected === 'choose') return <><Heading title={carrierContext ? `حركة مالية · ${allowedTarget.name}` : "حركة مالية جديدة"} subtitle="اختر اللي صار عشان نعرض لك الخانات المناسبة."/>
    <div className="panel quick flow"><h2>وش صار للمبلغ؟</h2>
      {movementChoice('in')}{movementChoice('out')}
      {!carrierContext && onPurchase && <button type="button" className="quickbtn" disabled={context.status !== 'active'} onClick={onPurchase}>
        <IconBox name="receipt-text" tone="party"/>شراء آجل من مورد<span className="next"><Icon name="chevron-left" className="smallicon"/></span>
      </button>}
      {!carrierContext && movementChoice('move')}
      {!carrierContext && onRefund && (granted.has('customer.refund_request') || granted.has('customer.refund_execute')) && <button type="button" className="quickbtn" onClick={onRefund}><IconBox name="corner-down-left" tone="c-out" />إرجاع مبلغ لعميل برقم الطلب</button>}
    </div></>;
  return <MovementFields key={selected} context={context} resource={allowedTarget} mode={selected} onBack={onBack} onChangeMode={() => setSelected('choose')} onSaved={onSaved} />;
}

function MovementFields({ context, resource, mode, onBack, onChangeMode, onSaved }) {
  const demand = context.demandOnly === true;
  const resourceIsSource = cash(resource) && (!transfer(mode) || mode === 'move');
  const [pickedTarget, setPickedTarget] = useState(!resourceIsSource && canTarget(context, resource, mode) ? resource : null);
  const [pickedSource, setPickedSource] = useState(resourceIsSource ? resource : null);
  const targets = (demand ? [pickedTarget].filter(Boolean) : context.resources).filter(item => canTarget(context, item, mode));
  const payout = mode === 'out';
  const fixedPayee = payout && resource && !resourceIsSource;
  const [payeeKind, setPayeeKind] = useState(fixedPayee ? resource.kind : '');
  const selectedTargets = payout ? targets.filter(item => item.kind === payeeKind) : targets;
  const [targetKey, setTargetKey] = useState(!resourceIsSource && targets.some(r => resourceKey(r) === resourceKey(resource || {})) ? resourceKey(resource) : '');
  const target = selectedTargets.find(r => resourceKey(r) === targetKey);
  const sources = sourcesFor(context, target, mode);
  const [sourceKind, setSourceKind] = useState(resourceIsSource ? resource.kind : '');
  const [sourceKey, setSourceKey] = useState(resourceIsSource ? resourceKey(resource) : '');
  const legacyGroup = usePaymentSourceGroup(context, sources, sourceKind, !demand && payout && Boolean(target));
  const group = demand ? { rows: pickedSource ? [pickedSource] : [], onDemand: true } : legacyGroup;
  const source = group.rows.find(item => resourceKey(item) === sourceKey && (!payout || item.kind === sourceKind));
  const [form, setForm] = useState(baseForm), [review, setReview] = useState(null), [message, setMessage] = useState('');
  const [quote, setQuote] = useState(null), [quoting, setQuoting] = useState(false);
  const [recoverTarget, setRecoverTarget] = useState('');
  const ad = target?.ad_bank_funding, pos = target?.kind === 'pos_receivable';
  const bankArrival = pos || isPayPal(target);
  const quoteKey = JSON.stringify([targetKey, form.unit, form.amount, ad?.terms]);
  const visibleQuote = quote?.key === quoteKey ? quote : null;
  const editable = context.status === 'active';
  function change(patch) { setForm(value => ({ ...value, ...patch })); setMessage(''); }
  async function quoteAd() {
    const amount = decimalAmount(form.amount);
    if (!amount || !target || !ad) return setMessage('أدخل المبلغ أولًا.');
    setQuoting(true); setMessage('');
    try { setQuote({ ...await api.quoteAd({ party_id: target.id, currency: form.unit, amount }), key: quoteKey }); }
    catch (err) {setMessage(simpleBalanceError(err));}
    finally {setQuoting(false);}
  }
  function prepare(event) {
    event.preventDefault();
    if (!editable || !target || !source) return setMessage('اختر حسابين متاحين لهذه الحركة.');
    const amount = decimalAmount(form.amount), reference = form.reference.trim();
    if (!amount || !form.date || form.date > todaySA()) return setMessage('راجع المبلغ وتاريخ الحركة.');
    const command = { party_type: target.kind, party_id: target.id, side: side(mode), amount,
      business_date: form.date, note: form.note.trim(), source_type: source.kind, source_id: source.id };
    const reason = reasonFor(target, mode);
    if (reason) command.reason = reason;
    if (reference) command.reference = reference;
    if (source.kind === 'bank' && target.prepaid_shipping_cash && reference.length < 3) return setMessage('أدخل مرجع عملية شحن الرصيد من البنك.');
    if (bankArrival) {
      if (!form.arrival || !reference) return setMessage('طابق وصول التحويل واكتب مرجعه.');
      command.bank_arrival_confirmed = true;
      if (pos && form.fee.trim()) {
        if (minorUnits(form.fee) === null || minorUnits(form.fee) < 0n || !form.receipt.trim()) return setMessage('راجع رسوم الشبكة ومرجع مستند الرسوم.');
        command.actual_fee_amount = form.fee.trim(); command.fee_reference = form.receipt.trim();
      }
    }
    if (ad) {
      const manual = form.unit === 'receipt';
      const usd = decimalAmount(manual ? form.usd : visibleQuote?.amount_usd);
      const gross = decimalAmount(manual ? amount : visibleQuote?.gross_sar);
      const fee = minorUnits(manual ? form.fee : visibleQuote?.fee_sar);
      if (!usd || !gross || fee === null || fee < 0n || fee >= minorUnits(gross)) return setMessage('احسب المقابل أو أدخل مبالغ الإيصال الصحيحة.');
      if (reference.length < 3) return setMessage('أدخل مرجع العملية البنكية.');
      command.amount = usd;
      command.ad_bank_funding = { bank_amount: gross, bank_fee_amount: `${fee / 100n}.${String(fee % 100n).padStart(2, '0')}`,
        kind: ad.mode === 'prepaid' || (ad.mode === 'hybrid' && !ad.wallet_first && form.wallet) ? 'wallet_funding' : 'payment' };
    }
    if (payout && ((group.onDemand && sourceAvailability(source) !== true) || exceedsSource(source, command.ad_bank_funding?.bank_amount || amount))) return setMessage(insufficientSource);
    setMessage(''); setReview(command);
  }
  function clearSource() { setSourceKey(''); setSourceKind(''); setPickedSource(null); }
  function changePayeeKind(value) {
    setPayeeKind(value); setTargetKey(''); setPickedTarget(null); clearSource(); setQuote(null);
    change({ unit: 'receipt', usd: '', fee: '', receipt: '', arrival: false, wallet: false });
  }
  const targetSelect = <select value={targetKey} required disabled={!editable} onChange={e => {
    setTargetKey(e.target.value); if (payout && targetKey) clearSource(); setQuote(null); change({ unit: 'receipt', usd: '', fee: '', receipt: '', arrival: false, wallet: false });
  }}>{options(selectedTargets)}</select>;
  const sourceSelect = <select value={source ? sourceKey : ''} required disabled={!editable} onChange={e => {setSourceKey(e.target.value); setMessage('');}}>{options(sources)}</select>;
  const demandTarget = label => <WorkspaceResourcePicker key={`recipient-${payeeKind}`} context={context} operation={mode} role="recipient"
    kind={payout ? payeeKind : ''} selectedResource={resourceIsSource ? source : undefined} value={target} label={label} disabled={!editable}
    onSelect={item => {
      setPickedTarget(item); setTargetKey(item ? resourceKey(item) : ''); clearSource(); setQuote(null);
      change({ unit: 'receipt', usd: '', fee: '', receipt: '', arrival: false, wallet: false });
    }}/>;
  const fundingKinds = target?.ad_bank_funding || isPayPal(target) || target?.kind === 'pos_receivable'
    ? ['bank'] : ['bank', 'cash', ...(payout && ['supplier', 'external_person', 'operating_expense'].includes(target?.kind)
      && permittedOperations(context).has('custody.spend') ? ['employee_custody'] : [])];
  const demandSource = label => <>
    {field('نوع مصدر الدفع', <select value={sourceKind} required disabled={!editable} onChange={event => {
      setSourceKind(event.target.value); setSourceKey(''); setPickedSource(null); setMessage('');
    }}><option value="">اختر نوع المصدر</option>{fundingKinds.map(kind => <option key={kind} value={kind}>{resourceLabels[kind]}</option>)}</select>)}
    {sourceKind && <WorkspaceResourcePicker key={`source-${sourceKind}-${targetKey}`} context={context} operation={mode} role="source" kind={sourceKind}
      selectedResource={target} value={source} label={label} disabled={!editable}
      optionDisabled={item => payout && sourceAvailability(item) !== true}
      onSelect={item => { setPickedSource(item); setSourceKey(item ? resourceKey(item) : ''); setMessage(''); }}/>}
  </>;
  const payoutKinds = demand ? Object.keys(resourceLabels).filter(kind => canTarget(context,
    { kind, id: '', currency: 'SAR', access: { use: true } }, 'out')) : [...new Set(targets.map(item => item.kind))];
  const sourceName = mode === 'in' ? target?.name : source?.name, destName = mode === 'in' ? source?.name : target?.name;
  return <><button className="textbtn back" type="button" onClick={onBack}><Icon name="chevron-left"/>الرئيسية</button>
    <Heading title={entryTitle(mode, target)} subtitle={review ? 'تأكد من الحساب والمبلغ قبل تسجيل الحركة.' : 'أدخل المبلغ وحدد من وين خرج ووين وصل.'} />
    {target?.kind === 'courier' && <p className="note" role="note">{mode === 'in'
      ? 'سجّل المبلغ الذي وصل فعليًا إلى البنك أو الصندوق فقط. يبقى الباقي في حساب الشركة؛ لا تخصم أجور الشحن تلقائيًا من التحصيل.'
      : 'سجّل سدادًا فعليًا من البنك أو الصندوق لأجور الشركة. الأجرة المسددة من محفظة الشحن لا تُسدد مرة ثانية.'} المقاصة بين التحصيل والأجور تحتاج تسوية موثقة مستقلة، ولا تُسجل كتحويل نقدي وهمي.</p>}
    <div className="flow"><div className="wizard">{review ? <span>١ · تفاصيل المبلغ</span> : <b>١ · تفاصيل المبلغ</b>}<i/>{review ? <b>٢ · مراجعة وحفظ</b> : <span>٢ · مراجعة وحفظ</span>}</div>
    {!editable && <p className="note warning">تسجيل الحركات غير متاح في حالة النظام الحالية.</p>}
    {!review && payout && target && recoverTarget !== targetKey && <PendingPayoutLink key={targetKey} context={context} target={target} onOpen={() => setRecoverTarget(targetKey)} />}
    {!review && target && recoverTarget === targetKey ? <Suspense fallback={<p role="status">جارٍ فتح العملية السابقة…</p>}>
      <OriginalEntry context={context} resource={target} onCancel={() => setRecoverTarget('')} onSaved={onSaved} /></Suspense> : !review ? <form className="panel form" onSubmit={prepare}><h2>تفاصيل المبلغ</h2><div className="fields">
      {payout ? <>
        {fixedPayee ? <div className="field"><span>وصل المبلغ إلى</span><strong>{resource.name}</strong></div> : <>
          {field('نوع السداد', <select value={payeeKind} required disabled={!editable} onChange={e => changePayeeKind(e.target.value)}>
            <option value="">اختر نوع المستفيد</option>{payoutKinds.map(kind =>
              <option key={kind} value={kind}>{resourceLabels[kind]}</option>)}
          </select>)}
          {payeeKind && (demand ? demandTarget('وصل المبلغ إلى') : field('وصل المبلغ إلى', targetSelect))}
        </>}
        {target && (demand ? demandSource('خرج المبلغ من') : <PaymentSourceFields sources={sources} group={group} kind={sourceKind} value={source ? sourceKey : ''} disabled={!editable}
          label="خرج المبلغ من" onKind={kind => { setSourceKind(kind); setSourceKey(''); setMessage(''); }}
          onSource={key => { setSourceKey(key); setMessage(''); }} />)}
      </> : <>
        {demand ? <>
          {resource && !resourceIsSource ? <div className="field"><span>{mode === 'in' ? 'استلمت من' : 'وصل المبلغ إلى'}</span><strong>{resource.name}</strong></div>
            : demandTarget(mode === 'in' ? 'استلمت من' : mode === 'move' ? 'وصل المبلغ إلى حساب' : 'وصل المبلغ إلى')}
          {target && demandSource(mode === 'in' ? 'دخل المبلغ في' : 'خرج المبلغ من')}
        </> : <>
          {field(mode === 'in' ? 'استلمت من' : 'خرج المبلغ من', mode === 'in' ? targetSelect : sourceSelect)}
          {field(mode === 'in' ? 'دخل المبلغ في' : mode === 'move' ? 'وصل المبلغ إلى حساب' : 'وصل المبلغ إلى', mode === 'in' ? sourceSelect : targetSelect)}
        </>}
      </>}
      {ad?.terms && field('طريقة إدخال الدفعة', <select value={form.unit} onChange={e => change({ unit: e.target.value, amount: '' })}><option value="receipt">من الإيصال البنكي</option><option value="SAR">احسب من المبلغ بالريال</option><option value="USD">احسب من المبلغ بالدولار</option></select>)}
      {field(ad ? form.unit === 'USD' ? 'المبلغ بالدولار' : 'إجمالي ما خُصم من البنك (ريال)' : bankArrival ? 'صافي المبلغ الوارد إلى البنك (ريال)' : target?.kind === 'courier' && mode === 'in' ? 'المبلغ المستلم فعليًا (ريال)' : 'المبلغ (ريال)', <input inputMode="decimal" value={form.amount} required placeholder="0.00" onChange={e => change({ amount: e.target.value })} />)}
      {field('تاريخ الحركة', <input type="date" value={form.date} max={todaySA()} required onChange={e => change({ date: e.target.value })} />)}
      {ad && (form.unit === 'receipt' ? <>{field('المبلغ الذي وصل للمنصة (دولار)', <input required inputMode="decimal" value={form.usd} onChange={e => change({ usd: e.target.value })} />)}{field('الرسوم ضمن مبلغ البنك (ريال)', <input required inputMode="decimal" value={form.fee} onChange={e => change({ fee: e.target.value })} />)}</> :
        <div className="field"><button type="button" className="button" disabled={quoting} onClick={quoteAd}>احسب المقابل</button>{visibleQuote && <span>خصم البنك {visibleQuote.gross_sar} ريال، وصل للمنصة {visibleQuote.amount_usd} دولار.</span>}</div>)}
      {ad?.mode === 'hybrid' && !ad.wallet_first && field('نوع الدفعة', <select value={form.wallet ? 'wallet' : 'payment'} onChange={e => change({ wallet: e.target.value === 'wallet' })}><option value="payment">سداد المستحقات</option><option value="wallet">شحن المحفظة</option></select>)}
      {field(ad || bankArrival || target?.prepaid_shipping_cash && source?.kind === 'bank' ? 'مرجع العملية البنكية' : 'رقم مرجع أو طلب (اختياري)', <input maxLength="160" value={form.reference} onChange={e => change({ reference: e.target.value })} />)}
      {field('بيان الحركة (اختياري)', <input maxLength="1000" value={form.note} onChange={e => change({ note: e.target.value })} />)}
      {pos && <>{field('رسوم الشبكة الفعلية إن وجدت', <input inputMode="decimal" value={form.fee} onChange={e => change({ fee: e.target.value })} />)}{form.fee && field('مرجع مستند الرسوم', <input required value={form.receipt} onChange={e => change({ receipt: e.target.value })} />)}</>}{bankArrival && <label><input type="checkbox" required checked={form.arrival} onChange={e => change({ arrival: e.target.checked })} /> طابقت وصول صافي التحويل إلى البنك</label>}
    </div>{message && <p className="note warning" role="alert">{message}</p>}
      <p className="note">{sourceName && destName && decimalAmount(form.amount) ? `من ${sourceName} إلى ${destName}.` : 'بعد إدخال التفاصيل يظهر لك أثر المبلغ على الحساب.'}</p>
      <div className="form-actions"><button className="button primary" type="submit" disabled={!editable}><Icon name="list-checks"/>مراجعة الحركة</button><button type="button" className="button" onClick={onChangeMode}>تغيير نوع الحركة</button></div>
    </form> : !target ? <p role="alert" className="note warning">تغيّرت صلاحيات الحساب. أعد فتح الحركة.</p> : <ReviewedMovement context={demand ? {...context,resources:[target,source].filter(Boolean)} : context} target={target} command={review} onEdit={() => setReview(null)} onSaved={onSaved} />}</div></>;
}

function PendingPayoutLink({ context, target, onOpen }) {
  const [marked, setMarked] = useState(false);
  const identity = JSON.stringify(['entry', context.actor.id, context.session_scope, target.kind, target.id]);
  useEffect(() => {
    let active = true;
    pendingBalanceStorageKey(identity).then(key => {
      if (active) setMarked(Boolean(localStorage.getItem(key)));
    }).catch(() => {});
    return () => { active = false; };
  }, [identity]);
  return marked ? <button type="button" className="button" onClick={onOpen}>مراجعة عملية سابقة لهذا الحساب</button> : null;
}

// Only the command's transport identity and schema defaults may differ.
// Keep real changes (especially the source account) distinct.
function entryIntent(command) {
  const amountFields = ['amount', 'actual_fee_amount', 'bank_amount', 'bank_fee_amount'];
  const canonical = (value, key) => {
    if (typeof value === 'string') {
      const trimmed = value.trim();
      const amount = amountFields.includes(key) ? minorUnits(trimmed) : null;
      return amount === null ? trimmed : String(amount);
    }
    if (Array.isArray(value)) return value.map(item => canonical(item));
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k], k)]));
    return value;
  };
  const body = Object.fromEntries(Object.entries(command).filter(([key, value]) =>
    !['request_id', 'expected_session_scope'].includes(key) && value != null
    && !(['note', 'reference'].includes(key) && !String(value).trim())));
  return JSON.stringify(canonical(body));
}

function ReviewedMovement({ context, target, command, onEdit, onSaved }) {
  const [restoreOriginal, setRestoreOriginal] = useState(false);
  const mutation = usePendingBalanceCommand({ context, storageIdentity: JSON.stringify(['entry', context.actor.id, context.session_scope, target.kind, target.id]),
    validate: value => value.action === 'entry' && value.target_id === null && value.command.party_type === target.kind && value.command.party_id === target.id,
    execute: value => api.entry(value.command), validateSaved: (result, record) => Boolean(result.movement_id)
      && result.party_id === record.command.party_id && result.party_type === record.command.party_type, onSaved, onRetired: onEdit });
  const previous = mutation.pending?.command, item = previous || command;
  const compatible = !previous || entryIntent(previous) === entryIntent(command);
  const resource = context.resources.find(r => r.kind === item.source_type && r.id === item.source_id);
  const requestedSource = context.resources.find(r => r.kind === command.source_type && r.id === command.source_id);
  const incoming = item.side === 'for_party';
  const entries = [['من', incoming ? target.name : resource?.name], ['إلى', incoming ? resource?.name : target.name],
    ['المبلغ', <Amount value={item.amount} currency={target.currency}/>], ['تاريخ الحركة', item.business_date], ['يسجّلها', context.actor.name],
    ['المرجع', item.reference], ['الملاحظة', item.note], ['خصم البنك (ريال)', item.ad_bank_funding?.bank_amount],
    ['رسوم التحويل (ريال)', item.ad_bank_funding?.bank_fee_amount || item.actual_fee_amount], ['مرجع الرسوم', item.fee_reference]];
  if (restoreOriginal) return <div className="panel form"><Suspense fallback={<p role="status">جارٍ فتح العملية السابقة…</p>}><OriginalEntry context={context} resource={target} onSaved={onSaved} onCancel={onEdit} /></Suspense></div>;
  return <section className="panel form"><h2>راجع الحركة</h2>{mutation.recovering && <p role="status">جارٍ التحقق من العملية السابقة…</p>}
    {previous && <p className="note warning">تخص التفاصيل أدناه الطلب السابق. يمكنك التحقق من نتيجته قبل المتابعة.</p>}
    {!compatible && <p className="note warning" role="alert">طلبك الحالي من {requestedSource?.name || 'الحساب المختار'} بمبلغ <Amount value={command.amount} currency={target.currency}/> يختلف عن الطلب السابق من {resource?.name || 'الحساب السابق'} بمبلغ <Amount value={item.amount} currency={target.currency}/>. <button type="button" className="textbtn" onClick={() => setRestoreOriginal(true)}>عرض العملية الأصلية</button></p>}
    {mutation.error && <p className="note warning" role="alert">{mutation.error}</p>}
    {mutation.notice && <p className="note" role="status">{mutation.notice}</p>}
    {compatible && <p className="note">يخرج <Amount value={item.ad_bank_funding?.bank_amount || item.amount} currency={resource?.currency || 'SAR'}/> من {incoming ? target.name : resource?.name} ويصل <Amount value={item.amount} currency={target.currency}/> إلى {incoming ? resource?.name : target.name}.</p>}
    <dl className="detail-list">{entries.filter(([,value]) => value).map(([k,v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    <div className="form-actions">{mutation.blocked ? <button className="button" disabled={mutation.recovering} type="button" onClick={mutation.recover}>استعادة العملية السابقة</button> : <button className="button primary" type="button" disabled={!compatible || !resource || context.status !== 'active' || mutation.busy || mutation.recovering} onClick={() => mutation.submit('entry', null, () => command)}><Icon name="check"/>{mutation.busy ? 'جارٍ تأكيد الحفظ…' : previous ? 'إعادة محاولة العملية نفسها' : 'تأكيد وتسجيل الحركة'}</button>}
      <PendingEntryActions mutation={mutation} />
      <button type="button" className="button" disabled={mutation.busy} onClick={onEdit}><Icon name="pencil"/>تعديل التفاصيل</button></div>
  </section>;
}
