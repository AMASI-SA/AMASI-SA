// Isolated synthetic browser harness. Never imported by the application entry.
import React from 'react';
import {createRoot} from 'react-dom/client';
import OperationalFinancialPosition from '../../src/pages/operational/OperationalFinancialPosition';
import '../../src/index.css';

createRoot(document.getElementById('root')).render(<>
  <p style={{margin:0,padding:12,background:'#fff1c2',textAlign:'center',fontWeight:700}}>معاينة محلية للتصميم — بيانات اختبارية وليست أرصدة فعلية</p>
  <OperationalFinancialPosition />
</>);
