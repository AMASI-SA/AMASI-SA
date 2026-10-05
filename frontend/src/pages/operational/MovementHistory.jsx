import React, {useEffect, useState} from 'react';
import {operationalApi as api, messageFor} from './api';

export default function MovementHistory() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    setItems(null); setError('');
    api.movements().then(result => { if (alive) setItems(result.items); })
      .catch(e => { if (alive) setError(messageFor(e)); });
    return () => { alive = false; };
  }, [revision]);
  return <section aria-label="الحركات المحفوظة">
    <h2>الحركات المحفوظة</h2>
    <button onClick={() => setRevision(value => value + 1)}>تحديث الحركات</button>
    {error ? <p role="alert">{error}</p> : items === null ? <p role="status">جاري قراءة الحركات…</p> : !items.length ? <p>لا توجد حركات محفوظة بعد.</p> :
      <div className="op-table"><table><thead><tr>{['التاريخ', 'الاتجاه', 'الجهة', 'الحساب', 'المبلغ', 'المصدر', 'المرجع', 'الإيصال'].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{items.map(row => <tr key={row.id}>
          <td>{row.occurred_at ? new Date(row.occurred_at).toLocaleString('ar-SA') : 'غير متاح'}</td>
          <td>{row.direction === 'incoming' ? 'وارد' : 'صادر'}</td><td>{row.name}</td><td>{row.bank_name || '—'}</td>
          <td>{row.amount} {row.currency}</td><td>{row.source === 'employee_app' ? 'تطبيق الموظفين' : 'ميزان 2'}</td>
          <td>{row.reference || '—'}</td><td>{row.receipt_id ? 'مرفق' : '—'}</td>
        </tr>)}</tbody></table></div>}
  </section>;
}
