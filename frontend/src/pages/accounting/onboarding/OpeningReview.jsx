import React from 'react';

const pending = 'غير مثبت';
const text = value => value === undefined || value === null || value === '' ? pending : String(value);
const money = value => /^-?\d+(\.\d{1,2})?$/.test(String(value ?? '')) ? BigInt(String(value).replace('-', '').split('.')[0]) * 100n + BigInt((String(value).split('.')[1] || '').padEnd(2, '0')) : null;
const format = amount => `${amount < 0n ? '-' : ''}${(amount < 0n ? -amount : amount) / 100n}.${String((amount < 0n ? -amount : amount) % 100n).padStart(2, '0')}`;
function difference(entries) {
    let amount = 0n;
    for (const row of entries) {
        const value = money(row.sar_amount);
        if (value === null || !['debit', 'credit'].includes(row.side)) return pending;
        amount += row.side === 'debit' ? value : -value;
    }
    return format(amount);
}
const Data = ({label, value}) => <div className="min-w-0 rounded-lg border bg-slate-50 p-3"><dt className="text-xs text-slate-600">{label}</dt><dd className="mt-1 break-all font-mono text-sm"><bdi>{text(value)}</bdi></dd></div>;

// Read-only projection of the server snapshot. It never signs, approves,
// rebalances or executes the journal shown below.
export default function OpeningReview({session, dirty = false, readiness}) {
    const preview = session?.preview;
    const entries = Array.isArray(preview?.entries) ? preview.entries : [];
    const evidence = Array.isArray(preview?.evidence) ? preview.evidence : [];
    const reviewed = !dirty && ['reviewed', 'handed_off'].includes(session?.status) && Boolean(session.reviewed_hash) && session.reviewed_hash === preview?.hash;
    return <section aria-label="تفاصيل المراجعة النهائية" className="space-y-4">
        <h3 className="text-lg font-bold">Snapshot · المراجعة النهائية</h3>
        <dl className="grid gap-3 sm:grid-cols-2">
            <Data label="الجلسة / Snapshot" value={session?.id} /><Data label="Version" value={session?.version} />
            <Data label="Snapshot hash" value={preview?.hash} /><Data label="Reviewed hash" value={session?.reviewed_hash} />
            <Data label="المراجع المسجل" value={session?.reviewed_by} /><Data label="وقت المراجعة" value={session?.reviewed_at} />
        </dl>
        <p role="status" className="rounded-lg bg-amber-50 p-3">اعتماد المالك التجاري: غير مثبت في عقد الجلسة. مراجعة الصلاحية {reviewed ? 'مثبتة للبصمة المحفوظة' : 'غير مثبتة للبصمة الحالية'}. توازن القيد وحده لا يثبت صحة الأرقام أو الجرد أو اعتماد المالك.</p>
        {dirty && <p role="alert">توجد تعديلات غير محفوظة؛ المعروض أدناه لقطة سابقة ولا يمثل الإدخال الحالي. احفظ ثم أعد المعاينة.</p>}
        {!preview ? <p>لم تُنشأ معاينة محفوظة بعد. أكمل الأقسام والأدلة ثم اطلب المعاينة من الخادم.</p> : <>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Data label="مدين SAR" value={preview.debit_total} /><Data label="دائن SAR" value={preview.credit_total} /><Data label="الفرق قبل مقابل حقوق الملكية SAR" value={Array.isArray(preview.entries) ? difference(entries.filter(e => e.category !== 'opening_equity_counterpart')) : undefined} /><Data label="الفرق النهائي SAR" value={Array.isArray(preview.entries) ? difference(entries) : undefined} /></dl>
            <h4 className="font-bold">أطراف القيد المقترحة — لم تُرحّل</h4>
            {!entries.length ? <p>لا توجد أطراف قيد في اللقطة المستلمة؛ راجع إثباتات الصفر الصريح والأدلة.</p> : <div className="overflow-x-auto"><table className="w-full text-right text-sm" aria-label="أطراف القيد"><thead><tr>{['الحساب / الهوية canonical', 'التصنيف', 'المبلغ الأصلي', 'FX: المعدل / الوقت / المصدر', 'مدين SAR', 'دائن SAR', 'الدليل'].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead><tbody>{entries.map(entry => <tr className="border-t align-top" key={entry.line_no}><td className="p-2">{entry.label}<br /><bdi>{entry.entity_type} / {entry.entity_id} / {entry.sub_account}</bdi>{entry.financial_account_id && <p><bdi>{entry.financial_account_id}</bdi></p>}</td><td>{entry.category}</td><td><bdi>{entry.original_amount} {entry.original_currency}</bdi></td><td><bdi>{text(entry.fx_snapshot?.rate_to_sar)} / {text(entry.fx_snapshot?.fx_at)} / {text(entry.fx_snapshot?.source)}</bdi></td><td>{entry.side === 'debit' ? entry.sar_amount : '—'}</td><td>{entry.side === 'credit' ? entry.sar_amount : '—'}</td><td><bdi>{text(entry.evidence_file_id)}</bdi></td></tr>)}</tbody></table></div>}
            <h4 className="font-bold">Evidence · Fingerprints</h4>
            {!evidence.length ? <p>لم تصل بصمات الأدلة من الخادم.</p> : <div className="overflow-x-auto"><table className="w-full text-right text-sm" aria-label="بصمات الأدلة"><thead><tr><th>المرجع</th><th>الغرض / القسم</th><th>المالك</th><th>SHA256</th><th>الحجم</th></tr></thead><tbody>{evidence.map(item => <tr className="border-t" key={`${item.source_file_id}:${item.purpose}:${item.section_id}`}><td><bdi>{item.source_file_id}</bdi></td><td>{item.purpose} / {text(item.section_id)}</td><td><bdi>{text(item.owner_id)}</bdi></td><td className="max-w-64 break-all font-mono"><bdi>{text(item.sha256)}</bdi></td><td>{text(item.size)}</td></tr>)}</tbody></table></div>}
            <p>مطابقة التقييم المالي: {preview.inventory_reconciliation?.verified === true ? 'مثبتة' : pending} · اعتماد الجرد الفعلي: {readiness?.inventory_physical_approval_verified === true ? 'مثبت وفق المصدر' : pending}</p>
            <details><summary className="cursor-pointer font-bold">تفاصيل اللقطة والتعيينات المحفوظة</summary><pre dir="ltr" className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-50 p-3 text-xs">{JSON.stringify(preview, null, 2)}</pre></details>
        </>}
        <p className="text-sm text-slate-600">Opening وتهيئة المخزون وActivation/P08 تحتاج مساراتها وتفويضها المستقل. هذه الصفحة لا تنفذها.</p>
    </section>;
}
