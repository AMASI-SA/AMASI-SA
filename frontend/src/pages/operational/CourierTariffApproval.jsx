import { useState } from 'react';
import api from '../../lib/api';

const endpoint = '/operational-balances/courier-tariffs';

export default function CourierTariffApproval() {
    const [items, setItems] = useState(null);
    const [cashAccounts, setCashAccounts] = useState([]);
    const [selected, setSelected] = useState('');
    const [cashAccountId, setCashAccountId] = useState('');
    const [editingBinding, setEditingBinding] = useState(false);
    const [date, setDate] = useState('');
    const [reason, setReason] = useState('');
    const [message, setMessage] = useState('');
    const [busy, setBusy] = useState(false);
    const item = items?.find(row => row.id === selected);
    const prepaid = item?.payment_mode === 'prepaid';
    const selectedCash = cashAccounts.find(account => account.id === cashAccountId);
    const currentCash = cashAccounts.find(account => account.id === item?.cash_account_id);
    const canApprove = !!item?.source_hash && !item.unavailable && !busy && !!date && reason.trim().length >= 3
        && (!prepaid || (!!selectedCash && (!item.same_terms || cashAccountId !== item.cash_account_id)));
    const resetSelection = () => {
        setDate(''); setReason(''); setCashAccountId(''); setEditingBinding(false); setMessage('');
    };
    const review = async () => {
        setBusy(true);
        resetSelection();
        setItems(null);
        setSelected('');
        setCashAccounts([]);
        try {
            const { data } = await api.get(endpoint);
            setItems(data.items);
            setCashAccounts((data.cash_accounts || []).filter(account => account.kind === 'cash'
                && account.currency === 'SAR' && account.ready === true
                && (!account.status || account.status === 'active')));
        } catch (err) {
            setMessage(err.response?.data?.detail?.message || 'تعذر فتح التعرفات المحفوظة.');
        } finally { setBusy(false); }
    };
    const approve = async () => {
        if (!canApprove) return;
        setBusy(true);
        try {
            await api.post(endpoint, { party_id: item.id, source_hash: item.source_hash,
                request_id: crypto.randomUUID(), confirmed: true, effective_from: date + ':00+03:00', reason: reason.trim(),
                ...(prepaid ? { cash_account_id: cashAccountId } : {}) });
            setSelected(''); setItems(null); resetSelection(); setCashAccounts([]);
            setMessage(prepaid
                ? 'اعتُمدت التعرفة وحُفظ ربط الصندوق. تظهر أجرة الشحن المدفوعة وخصم الصندوق للطلبات المستوفية عند إعادة احتساب المركز المالي.'
                : 'اعتُمدت التعرفة للاحتساب التشغيلي. تظهر الأجور المستوفية عند إعادة احتساب المركز المالي.');
        } catch (err) {
            setMessage(err.response?.data?.detail?.message || 'تعذر اعتماد التعرفة. افتح المعاينة مرة أخرى.');
        } finally { setBusy(false); }
    };
    return <section className="space-y-2 rounded-lg border p-4 bg-white" aria-label="تعرفة الشحن في المركز المالي">
        <h2 className="font-bold">تعرفات شركات الشحن في المركز المالي</h2>
        <p>راجع تعرفة شركة الشحن وحدد بداية سريانها. للشحن المدفوع مقدمًا، اختر الصندوق الذي تُخصم منه الأجرة عند انتقال الطلب إلى «تم التنفيذ».</p>
        <button type="button" disabled={busy} onClick={review}>مراجعة التعرفات المحفوظة</button>
        {items?.length === 0 && <p>لا توجد شركات شحن خارجية بإعداد محفوظ يمكن ربطه.</p>}
        {!!items?.length && <label className="block">شركة الشحن <select disabled={busy} value={selected} onChange={e => { setSelected(e.target.value); resetSelection(); }}>
            <option value="">اختر الشركة</option>{items.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>}
        {item && <div className="space-y-2">
        {item.salla_carrier_key && <p>رمز الشركة الأصلي في سلة: <bdi dir="ltr">{item.salla_carrier_key}</bdi>. يُعتمد هذا الرمز كما هو لربط الشحنة بالشركة.</p>}
        {item.salla_carrier_key && item.current_from && <p>هوية الشركة المعتمدة ثابتة؛ تعديل التعرفة أو الصندوق للفترة التالية يحفظ الأجور المثبتة سابقًا.</p>}
        <p>{prepaid
            ? 'دفع مقدم: تُسجّل أجرة الشحن مصروفًا مدفوعًا وتُخصم مرة واحدة من الصندوق عند «تم التنفيذ». انتقال الطلب لاحقًا إلى «تم التوصيل» لا يكرر الخصم.'
            : 'دفع آجل: تُحتسب أجرة الشحن عند انتقال حالة الطلب إلى «تم التوصيل» خلال فترة التشغيل.'}</p>
        {item.terms && <>
            <p>أجرة الشحنة {item.terms.cost_per_order} ر.س. وضريبتها {item.terms.vat_percent}٪.</p>
            {item.terms.cod_fee_tiers?.length ? <ul>{item.terms.cod_fee_tiers.map((tier, i) =>
                <li key={i}>من {tier.min_amount} {tier.min_inclusive ? '(شامل)' : '(غير شامل)'} إلى {tier.max_amount ?? 'بلا حد'} {tier.max_amount != null && (tier.max_inclusive ? '(شامل)' : '(غير شامل)')} ر.س. — {Number((tier.commission_percent * 100).toFixed(6))}٪ + {tier.fixed_fee} ر.س.، الضريبة {tier.vat_percent}٪ {tier.vat_included ? 'مشمولة' : 'إضافية'}.</li>)}</ul> :
                <p>{item.terms.cod_fee_percent != null ? `عمولة التحصيل ${Number((item.terms.cod_fee_percent * 100).toFixed(6))}٪ + ${item.terms.cod_fee_fixed_per_order} ر.س.، الضريبة ${item.terms.cod_fee_vat_percent}٪ ${item.terms.cod_fee_vat_included ? 'مشمولة' : 'إضافية'}.` : 'رسوم التحصيل غير مكتملة.'}</p>}
            <p>معرّفات البوليصة المرتبطة بالشركة {item.keys.join('، ')}</p>
        </>}
        {prepaid && item.cash_account_id && <p>الصندوق المعتمد: {currentCash?.name || 'الصندوق المسجل غير متاح للاختيار حاليًا'}.</p>}
        {item.unavailable ? <p>{item.unavailable}</p> : item.same_terms && !editingBinding ? <>
            <p>التعرفة الحالية معتمدة منذ {item.current_from}.</p>
            {prepaid && <button type="button" disabled={busy} onClick={() => setEditingBinding(true)}>تعديل ربط الصندوق للفترة التالية</button>}
        </> : <>
            {prepaid && <>
                <label className="block">صندوق خصم أجرة الشحن <select disabled={busy} value={cashAccountId} onChange={e => setCashAccountId(e.target.value)}>
                    <option value="">اختر الصندوق</option>
                    {cashAccounts.map(account => <option key={account.id} value={account.id}>{account.name} — ر.س.</option>)}
                </select></label>
                {cashAccounts.length === 0 && <p>لا يوجد صندوق نشط وجاهز بالريال السعودي. أكمل إعداد الصندوق ثم افتح مراجعة التعرفات مرة أخرى.</p>}
                {editingBinding && <p>يطبّق الصندوق الجديد من بداية السريان المحددة، وتحتفظ الأجور المدفوعة سابقًا بصندوقها المسجل.</p>}
            </>}
            <label className="block">بداية السريان بتوقيت الرياض <input type="datetime-local" value={date} onChange={e => setDate(e.target.value)} /></label>
            <label className="block">مرجع التعرفة أو سبب الاعتماد <input value={reason} maxLength={400} onChange={e => setReason(e.target.value)} /></label>
            <button type="button" disabled={!canApprove} onClick={approve}>اعتماد تعرفة هذه الشركة للفترة المحددة</button>
        </>}</div>}
        {message && <p role="status">{message}</p>}
    </section>;
}
