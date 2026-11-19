import { useState } from 'react';
import api from '../../lib/api';

export default function AramexOrderStatus({ sessionScope }) {
    const [input, setInput] = useState('');
    const [review, setReview] = useState(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState('');
    const numbers = [...new Set(input.trim().split(/[\s,،]+/).filter(Boolean))];
    async function submit(confirm) {
        if (pending || !numbers.length || !sessionScope || (confirm && !review)) return;
        setPending(true); setError('');
        try {
            const payload = { order_numbers: numbers, expected_session_scope: sessionScope };
            if (confirm) Object.assign(payload, {preview_hash: review.preview_hash, request_id: review.request_id});
            const { data } = await api.post('/operational-balances/courier-tariffs/aramex-order-status', payload);
            setReview({ ...data, request_id: confirm ? review.request_id : crypto.randomUUID() });
        } catch (e) {
            setError(e.response?.data?.detail?.message || 'تعذر التحقق من نتيجة المطابقة. أعد المحاولة بنفس الأرقام.');
        } finally { setPending(false); }
    }
    return <div className="space-y-2 border-t pt-3">
        <h3 className="font-semibold">خصم أرامكس حسب حالة طلب سلة</h3>
        <p>يشمل تم التنفيذ وجاري التوصيل وتم التوصيل بعد بداية التشغيل. يُخصم مبلغ العقد مرة واحدة حتى إذا أصبح رصيد الصندوق سالبًا. الاعتماد يفعّل القاعدة للطلبات اللاحقة.</p>
        <label className="block">أرقام الطلبات للمطابقة <textarea value={input} disabled={pending} onChange={e => {setInput(e.target.value); setReview(null); setError('');}} /></label>
        <button type="button" disabled={pending || !numbers.length || numbers.length > 250 || !sessionScope} onClick={() => submit(false)}>معاينة خصومات أرامكس</button>
        {review && <div role="status"><p>{review.applied ? 'خُصم' : 'جاهز للخصم'} {review.count} طلبًا بإجمالي {review.total} ر.س. · مسجّل سابقًا {review.existing} · مستبعد {review.skipped.length}</p>
            {review.skipped.length > 0 && <details><summary>الطلبات المستبعدة وأسبابها</summary><ul>{review.skipped.map(([number, reason]) => <li key={number}>{number} · {reason}</li>)}</ul></details>}
            {!review.applied && <button type="button" disabled={pending} onClick={() => submit(true)}>اعتماد القاعدة وخصم الطلبات المعروضة</button>}
        </div>}
        {error && <p role="alert">{error}</p>}
    </div>;
}
