import React from 'react';
import {createRoot} from 'react-dom/client';
import AccountingOnboarding from '../../../frontend/src/pages/accounting/onboarding/AccountingOnboarding';
import './styles.css';
const permissions = ['accounting.opening_balances.view', 'accounting.opening_balances.drafts.manage', 'accounting.opening_balances.review'];
createRoot(document.getElementById('root')).render(<><p dir="ltr" style={{background:'#fff3cd',padding:16}}>SYNTHETIC LOCAL STAGE 10 TEST — real FastAPI + disposable Mongo — no live data</p><AccountingOnboarding accountingPermissions={permissions}/></>);
