import React from 'react';
import './operationalToolTheme.css';

export default function OperationalToolBack() {
  return <nav className="mz-ops-back" aria-label="التنقل بين أدوات الأرصدة">
    <a href="/operational-balances?view=tools">العمليات والكشوف</a>
  </nav>;
}
