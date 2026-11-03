import { useState } from 'react';
import api from '../../lib/api';

const endpoint = '/operational-balances/courier-tariffs';

export default function CourierTariffApproval() {
    const [items, setItems] = useState(null);
    const [selected, setSelected] = useState('');
    const [date, setDate] = useState('');
    const [reason, setReason] = useState('');
    const [message, setMessage] = useState('');
    const [busy, setBusy] = useState(false);
    const item = items?.find(row => row.id === selected);
    const review = async () => {
        setBusy(true);
        setMessage('');
        setItems(null);
        setSelected('');
        try {
            const { data } = await api.get(endpoint);
            setItems(data.items);
        } catch (err) {
            setMessage(err.response?.data?.detail?.message || 'تعذر فتح التعرفات المحفوظة.');
        } finally { setBusy(false); }
    };
    const approve = async () => {
        if (!item?.source_hash || busy || !date || reason.trim().length < 3) return;
        setBusy(true);
        try {
            await api.post(endpoint, { party_id: item.id, source_hash: item.source_hash,
                request_id: crypto.randomUUID(), confirmed: true, effective_from: date + ':00+03:00', reason: reason.trim() });
            setSelected(''); setItems(null); setDate(''); setReason('');
            setMessage('اعتُمدت التعرفة للاحتساب التشغيلي. تظهر الأجور المستوفية عند إعادة احتساب المركز المالي.');
        } catch (err) {
            setMessage(err.response?.data?.detail?.message || 'تعذر اعتماد التعرفة. افتح المعاينة مرة أخرى.');
        } finally { setBusy(false); }
    };
    return <section className="space-y-2 rounded-lg border p-4 bg-white" aria-label="تعرفة الشحن في المركز المالي">
        <h2 className="font-bold">تعرفات شركات الشحن في المركز المالي</h2>
        <p>راجع التعرفة المحفوظة للشركة الآجلة وحدد متى بدأ العمل بها. تُعرض الأجور التاريخية المنطبقة للمطابقة مع الافتتاح والسداد.</p>
        <button type="button" disabled={busy} onClick={review}>مراجعة التعرفات المحفوظة</button>
        {items?.length === 0 && <p>لا توجد شركات شحن خارجية آجلة بإعداد محفوظ يمكن ربطه.</p>}
        {!!items?.length && <label className="block">شركة الشحن <select value={selected} onChange={e => { setSelected(e.target.value); setDate(''); setReason(''); setMessage(''); }}>
            <option value="">اختر الشركة</option>{items.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>}
        {item && <div className="space-y-2">{item.terms && <>
            <p>أجرة الشحنة {item.terms.cost_per_order} ر.س. وضريبتها {item.terms.vat_percent}٪.</p>
            {item.terms.cod_fee_tiers?.length ? <ul>{item.terms.cod_fee_tiers.map((tier, i) =>
                <li key={i}>من {tier.min_amount} {tier.min_inclusive ? '(شامل)' : '(غير شامل)'} إلى {tier.max_amount ?? 'بلا حد'} {tier.max_amount != null && (tier.max_inclusive ? '(شامل)' : '(غير شامل)')} ر.س. — {Number((tier.commission_percent * 100).toFixed(6))}٪ + {tier.fixed_fee} ر.س.، الضريبة {tier.vat_percent}٪ {tier.vat_included ? 'مشمولة' : 'إضافية'}.</li>)}</ul> :
                <p>{item.terms.cod_fee_percent != null ? `عمولة التحصيل ${Number((item.terms.cod_fee_percent * 100).toFixed(6))}٪ + ${item.terms.cod_fee_fixed_per_order} ر.س.، الضريبة ${item.terms.cod_fee_vat_percent}٪ ${item.terms.cod_fee_vat_included ? 'مشمولة' : 'إضافية'}.` : 'رسوم التحصيل غير مكتملة.'}</p>}
            <p>معرّفات البوليصة المرتبطة بالشركة {item.keys.join('، ')}</p>
        </>}
        {item.unavailable ? <p>{item.unavailable}</p> : item.same_terms ? <p>التعرفة الحالية معتمدة منذ {item.current_from}.</p> : <>
            <label className="block">بداية السريان بتوقيت الرياض <input type="datetime-local" value={date} onChange={e => setDate(e.target.value)} /></label>
            <label className="block">مرجع التعرفة أو سبب الاعتماد <input value={reason} maxLength={400} onChange={e => setReason(e.target.value)} /></label>
            <button type="button" disabled={busy || !date || reason.trim().length < 3} onClick={approve}>اعتماد تعرفة هذه الشركة للفترة المحددة</button>
        </>}</div>}
        {message && <p role="status">{message}</p>}
    </section>;
}
