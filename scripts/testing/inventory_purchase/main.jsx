import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {AuthProvider} from '../../../frontend/src/context/AuthContext.jsx';
import InventoryWorkspace from '../../../frontend/src/pages/InventoryWorkspace.jsx';

// No application component, stylesheet or API adapter is mocked. The isolated
// loopback server supplies synthetic JSON at the normal HTTP boundary only.
createRoot(document.getElementById('root')).render(
  <BrowserRouter><AuthProvider><InventoryWorkspace/></AuthProvider></BrowserRouter>,
);
