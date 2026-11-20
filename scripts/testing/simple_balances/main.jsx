import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../../../frontend/src/context/AuthContext.jsx';
import ApprovedFinance from '../../../frontend/src/pages/operational/ApprovedFinance.jsx';
import SimpleBalances from '../../../frontend/src/pages/operational/SimpleBalances.jsx';
import SimpleBalanceAccess from '../../../frontend/src/pages/operational/SimpleBalanceAccess.jsx';

// Actual application components and auth/API adapters. Only the loopback HTTP
// server supplies synthetic records; no application module is replaced.
createRoot(document.getElementById('root')).render(
  <BrowserRouter><AuthProvider><Routes>
    <Route path="/operational-balances" element={<ApprovedFinance />} />
    <Route path="/operational-balances/access" element={<SimpleBalanceAccess />} />
    <Route path="*" element={<SimpleBalances />} />
  </Routes></AuthProvider></BrowserRouter>,
);
