import React, { lazy, Suspense, useState } from 'react';
import { Amount, Heading, Icon, IconBox } from './ApprovedFinanceUI';
import { todaySA } from '../../lib/dates';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { usePendingBalanceCommand } from './simpleBalancesPending';
import { decimalAmount, entryChoices, isMoneyAccount, minorUnits, permittedOperations, resourceKey } from './simpleBalancesModel';

const RefundScreen = lazy(() => import('./ApprovedRefund'));
export function ApprovedRefund(props) {
  return <Suspense fallback={<p role="status">جارٍ فتح إرجاع المبلغ…</p>}><RefundScreen {...props} /></Suspense>;
}

const modes = {
  in: ['استلمت مبلغ', 'arrow-down-left', 'c-in'], out: ['دفعت مبلغ', 'arrow-up-right', 'c-out'],
  move: ['حوّلت بين حسابين', 'arrow-left-right', 'c-move'], ads: ['شحن رصيد إعلان', 'arrow-up-right', 'c-out'],
  wallet: ['شحن صندوق سلة', 'arrow-left-right', 'c-move'],
};
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
    if (cash(item)) return true;
    return mode === 'out' && ['supplier', 'external_person', 'operating_expense'].includes(target?.kind)
      && item.kind === 'employee_custody' && permittedOperations(context).has('custody.spend');
  });
}
const options = list => [<option key="" value="">اختر من القائمة</option>, ...list.map(r => <option key={resourceKey(r)} value={resourceKey(r)}>{r.name}</option>)];
const field = (label, control) => <label className="field"><span>{label}</span>{control}</label>;
const baseForm = () => ({ amount: '', reference: '', note: '', date: todaySA(), unit: 'receipt', usd: '', fee: '', receipt: '', arrival: false, wallet: false });

export default function ApprovedEntry(props) {
  return <EntryWorkflow key={JSON.stringify([props.context.actor.id, props.context.session_scope,
    props.context.permission_version, props.mode, props.resource?.kind, props.resource?.id])} {...props} />;
}

function EntryWorkflow({ context, resource, mode = 'choose', onBack, onSaved, onRefund }) {
  const [selected, setSelected] = useState(modes[mode] ? mode : 'choose');
  const resourceId = resource && resourceKey(resource);
  const allowedTarget = resource && context.resources.find(item => resourceKey(item) === resourceId);
  const granted = permittedOperations(context);
  const sources = context.resources.filter(item => item.access?.use === true && (item.currency || 'SAR') === 'SAR');
  const moneySources = sources.filter(cash).slice(0, 2);
  const bankSource = sources.some(r => r.kind === 'bank');
  const custodySource = sources.some(r => r.kind === 'employee_custody') && granted.has('custody.spend');
  const canFund = (r, key) => {
    if (r.ad_bank_funding || isPayPal(r) || r.kind === 'pos_receivable') return bankSource;
    return moneySources.some(item => resourceKey(item) !== resourceKey(r))
      || key === 'out' && custodySource && ['supplier', 'external_person', 'operating_expense'].includes(r.kind);
  };
  if (selected === 'choose') return <><Heading title="حركة مالية جديدة" subtitle="اختر اللي صار عشان نعرض لك الخانات المناسبة."/>
    <div className="panel quick flow"><h2>وش صار للمبلغ؟</h2>{['in', 'out', 'move'].map(key => {
      const [label, icon, tone] = modes[key];
      return <button key={key} type="button" className="quickbtn" disabled={context.status !== 'active' || !context.resources.some(r => canTarget(context, r, key) && canFund(r, key))} onClick={() => setSelected(key)}><IconBox name={icon} tone={tone}/>{label}<span className="next"><Icon name="chevron-left" className="smallicon" /></span></button>;
    })}{onRefund && (granted.has('customer.refund_request') || granted.has('customer.refund_execute')) && <button type="button" className="quickbtn" onClick={onRefund}><IconBox name="corner-down-left" tone="c-out" />إرجاع مبلغ لعميل برقم الطلب</button>}</div></>;
  return <MovementFields key={selected} context={context} resource={allowedTarget} mode={selected} onBack={onBack} onChangeMode={() => setSelected('choose')} onSaved={onSaved} />;
}

function MovementFields({ context, resource, mode, onBack, onChangeMode, onSaved }) {
  const resourceIsSource = cash(resource) && (!transfer(mode) || mode === 'move');
  const targets = context.resources.filter(item => canTarget(context, item, mode));
  const [targetKey, setTargetKey] = useState(!resourceIsSource && targets.some(r => resourceKey(r) === resourceKey(resource || {})) ? resourceKey(resource) : '');
  const target = targets.find(r => resourceKey(r) === targetKey);
  const sources = sourcesFor(context, target, mode);
  const [sourceKey, setSourceKey] = useState(resourceIsSource ? resourceKey(resource) : '');
  const source = sources.find(item => resourceKey(item) === sourceKey);
  const [form, setForm] = useState(baseForm), [review, setReview] = useState(null), [message, setMessage] = useState('');
  const [quote, setQuote] = useState(null), [quoting, setQuoting] = useState(false);
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
    setMessage(''); setReview(command);
  }
  const targetSelect = <select value={targetKey} required disabled={!editable} onChange={e => {
    setTargetKey(e.target.value); setQuote(null); change({ unit: 'receipt', usd: '', fee: '', receipt: '', arrival: false, wallet: false });
  }}>{options(targets)}</select>;
  const sourceSelect = <select value={source ? sourceKey : ''} required disabled={!editable} onChange={e => {setSourceKey(e.target.value); setMessage('');}}>{options(sources)}</select>;
  const sourceName = mode === 'in' ? target?.name : source?.name, destName = mode === 'in' ? source?.name : target?.name;
  return <><button className="textbtn back" type="button" onClick={onBack}><Icon name="chevron-left"/>الرئيسية</button>
    <Heading title={modes[mode][0]} subtitle={review ? 'تأكد من الحساب والمبلغ قبل تسجيل الحركة.' : 'أدخل المبلغ وحدد من وين خرج ووين وصل.'} />
    <div className="flow"><div className="wizard">{review ? <span>١ · تفاصيل المبلغ</span> : <b>١ · تفاصيل المبلغ</b>}<i/>{review ? <b>٢ · مراجعة وحفظ</b> : <span>٢ · مراجعة وحفظ</span>}</div>
    {!editable && <p className="note warning">تسجيل الحركات غير متاح في حالة النظام الحالية.</p>}
    {!review ? <form className="panel form" onSubmit={prepare}><h2>تفاصيل المبلغ</h2><div className="fields">
      {field(mode === 'in' ? 'استلمت من' : 'خرج المبلغ من', mode === 'in' ? targetSelect : sourceSelect)}
      {field(mode === 'in' ? 'دخل المبلغ في' : mode === 'move' ? 'وصل المبلغ إلى حساب' : 'وصل المبلغ إلى', mode === 'in' ? sourceSelect : targetSelect)}
      {ad?.terms && field('طريقة إدخال الدفعة', <select value={form.unit} onChange={e => change({ unit: e.target.value, amount: '' })}><option value="receipt">من الإيصال البنكي</option><option value="SAR">احسب من المبلغ بالريال</option><option value="USD">احسب من المبلغ بالدولار</option></select>)}
      {field(ad ? form.unit === 'USD' ? 'المبلغ بالدولار' : 'إجمالي ما خُصم من البنك (ريال)' : bankArrival ? 'صافي المبلغ الوارد إلى البنك (ريال)' : 'المبلغ (ريال)', <input inputMode="decimal" value={form.amount} required placeholder="0.00" onChange={e => change({ amount: e.target.value })} />)}
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
    </form> : !target ? <p role="alert" className="note warning">تغيّرت صلاحيات الحساب. أعد فتح الحركة.</p> : <ReviewedMovement context={context} target={target} command={review} onEdit={() => setReview(null)} onSaved={onSaved} />}</div></>;
}

function ReviewedMovement({ context, target, command, onEdit, onSaved }) {
  const [restoreOriginal, setRestoreOriginal] = useState(false);
  const mutation = usePendingBalanceCommand({ context, storageIdentity: JSON.stringify(['entry', context.actor.id, context.session_scope, target.kind, target.id]),
    validate: value => value.action === 'entry' && value.target_id === null && value.command.party_type === target.kind && value.command.party_id === target.id,
    execute: value => api.entry(value.command), validateSaved: (result, record) => Boolean(result.movement_id)
      && result.party_id === record.command.party_id && result.party_type === record.command.party_type, onSaved, onRetired: onEdit });
  const previous = mutation.pending?.command, item = previous || command;
  const compatible = !previous || Object.keys({...command, ...previous}).filter(k => !['request_id', 'expected_session_scope'].includes(k))
    .every(k => JSON.stringify(previous[k]) === JSON.stringify(command[k]));
  const resource = context.resources.find(r => r.kind === item.source_type && r.id === item.source_id);
  const incoming = item.side === 'for_party';
  const entries = [['من', incoming ? target.name : resource?.name], ['إلى', incoming ? resource?.name : target.name],
    ['المبلغ', <Amount value={item.amount} currency={target.currency}/>], ['تاريخ الحركة', item.business_date], ['يسجّلها', context.actor.name],
    ['المرجع', item.reference], ['الملاحظة', item.note], ['خصم البنك (ريال)', item.ad_bank_funding?.bank_amount],
    ['رسوم التحويل (ريال)', item.ad_bank_funding?.bank_fee_amount || item.actual_fee_amount], ['مرجع الرسوم', item.fee_reference]];
  if (restoreOriginal) return <div className="panel form"><Suspense fallback={<p role="status">جارٍ فتح العملية السابقة…</p>}><OriginalEntry context={context} resource={target} onSaved={onSaved} onCancel={onEdit} /></Suspense></div>;
  return <section className="panel form"><h2>راجع الحركة</h2>{mutation.recovering && <p role="status">جارٍ التحقق من العملية السابقة…</p>}
    {previous && <p className="note warning">هناك عملية سابقة تنتظر تأكيد النتيجة. التفاصيل المعروضة تخص العملية المحفوظة.</p>}
    {!compatible && <p className="note warning" role="alert">تختلف العملية المحفوظة عن المدخلات الجديدة. يمكنك استعادة العملية الأصلية ومراجعتها. <button type="button" className="textbtn" onClick={() => setRestoreOriginal(true)}>عرض العملية الأصلية</button></p>}
    {mutation.error && <p className="note warning" role="alert">{mutation.error}</p>}
    {compatible && <p className="note">يخرج <Amount value={item.ad_bank_funding?.bank_amount || item.amount} currency={resource?.currency || 'SAR'}/> من {incoming ? target.name : resource?.name} ويصل <Amount value={item.amount} currency={target.currency}/> إلى {incoming ? resource?.name : target.name}.</p>}
    <dl className="detail-list">{entries.filter(([,value]) => value).map(([k,v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    <div className="form-actions">{mutation.blocked ? <button className="button" disabled={mutation.recovering} type="button" onClick={mutation.recover}>استعادة العملية السابقة</button> : <button className="button primary" type="button" disabled={!compatible || !resource || context.status !== 'active' || mutation.busy || mutation.recovering} onClick={() => mutation.submit('entry', null, () => command)}><Icon name="check"/>{mutation.busy ? 'جارٍ تأكيد الحفظ…' : previous ? 'إعادة محاولة العملية نفسها' : 'تأكيد وتسجيل الحركة'}</button>}
      <button type="button" className="button" disabled={mutation.busy} onClick={onEdit}><Icon name="pencil"/>تعديل التفاصيل</button></div>
  </section>;
}
