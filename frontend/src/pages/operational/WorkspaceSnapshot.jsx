import React from 'react';

const timestamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Riyadh',
  }).format(new Date(value)) : null;

export default function WorkspaceSnapshot({ snapshot }) {
  if (!snapshot?.display_only) return null;
  const asOf = timestamp(snapshot.as_of), prepared = timestamp(snapshot.generated_at);
  if (!asOf && !prepared) return null;
  return <p className="under" aria-label="تاريخ البيانات المحفوظة">
    {asOf && <span>آخر احتساب للمصادر: {asOf} · الرياض</span>}
    {asOf && prepared && ' · '}
    {prepared && <span>تجهيز هذا العرض: {prepared} · الرياض</span>}
  </p>;
}
