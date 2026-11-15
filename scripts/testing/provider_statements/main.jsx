import React from 'react';
import {createRoot} from 'react-dom/client';
import {AuthProvider} from '../../../frontend/src/context/AuthContext.jsx';
import ProviderStatements from '../../../frontend/src/pages/operational/ProviderStatements.jsx';
import '../../../frontend/src/index.css';

createRoot(document.getElementById('root')).render(<AuthProvider><ProviderStatements /></AuthProvider>);
