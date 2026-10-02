import React from 'react';
import {createRoot} from 'react-dom/client';
import AccountingOnboarding from '../../../frontend/src/pages/accounting/onboarding/AccountingOnboarding';
import './styles.css';
const permissions = ['accounting.opening_balances.view','accounting.opening_balances.drafts.manage','accounting.opening_balances.review','accounting.shipping.view','accounting.rules.manage','accounting.shipping.contracts.review'];
createRoot(document.getElementById('root')).render(<><p dir="ltr" className="bg-amber-100 p-4">SYNTHETIC C5 BUSINESS ACCEPTANCE — one source register, one setup session — no opening, activation or live data</p><AccountingOnboarding accountingPermissions={permissions}/></>);
