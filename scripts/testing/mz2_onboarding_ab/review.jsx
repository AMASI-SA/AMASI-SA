import React from 'react';
import {createRoot} from 'react-dom/client';
import AccountingOnboarding from '../../../frontend/src/pages/accounting/onboarding/AccountingOnboarding';
import './styles.css';
const permissions = ['accounting.opening_balances.view', 'accounting.opening_balances.drafts.manage', 'accounting.opening_balances.review'];
if (__MZ2_AB_RICH_SHIPPING__) permissions.push('accounting.shipping.view', 'accounting.rules.manage', 'accounting.shipping.contracts.review');
createRoot(document.getElementById('root')).render(<><p dir="ltr" style={{background:'#fff3cd',padding:16}}>SYNTHETIC LOCAL A+B TEST — real FastAPI + disposable Mongo — no live data</p><AccountingOnboarding accountingPermissions={permissions}/></>);
