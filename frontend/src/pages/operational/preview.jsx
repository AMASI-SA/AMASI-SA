// Synthetic local browser harness; not referenced by the application entry.
import React from 'react';
import {createRoot} from 'react-dom/client';
import OperationalBalances from './OperationalBalances';
import CustomerReturns from './CustomerReturns';
const query=new URLSearchParams(location.search);
createRoot(document.getElementById('root')).render(query.get('view')==='customer-returns'?<CustomerReturns/>:<OperationalBalances source={query.get('source') === 'employee_app' ? 'employee_app' : 'mezan2'} />);
