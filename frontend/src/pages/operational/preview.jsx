// Synthetic local browser harness; not referenced by the application entry.
import React from 'react';
import {createRoot} from 'react-dom/client';
import OperationalBalances from './OperationalBalances';
createRoot(document.getElementById('root')).render(<OperationalBalances source={new URLSearchParams(location.search).get('source') === 'employee_app' ? 'employee_app' : 'mezan2'} />);
