import React from 'react';
import { useLocation } from 'react-router-dom';
import Layout from '../../components/Layout';
import ApprovedSpecializedPage from './ApprovedSpecializedPage';

// Inventory and shipping settings also belong to other workspaces. Only the
// explicit finance entry keeps them inside the operational finance shell.
export default function OperationalContextRoute({ children }) {
  const location = useLocation();
  const origin = new URLSearchParams(location.search).getAll('from');
  return origin.length === 1 && origin[0] === 'operational-tools'
    ? <ApprovedSpecializedPage>{children}</ApprovedSpecializedPage>
    : <Layout>{children}</Layout>;
}
