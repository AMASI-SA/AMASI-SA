import React from 'react';

export default function PendingEntryActions({ mutation, className = 'button' }) {
  if (!mutation.pending || mutation.blocked) return null;
  const disabled = mutation.busy || mutation.recovering;
  return <>
    <button type="button" className={className} disabled={disabled} onClick={mutation.recover}>تحقق من نتيجة العملية السابقة</button>
    {mutation.pending.resolution?.status === 'pending' && <button type="button" className={className} disabled={disabled} onClick={mutation.discard}>إلغاء الطلب السابق إذا لم يُنفّذ</button>}
  </>;
}
