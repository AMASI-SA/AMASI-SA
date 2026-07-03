import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { AuthProvider } from "@/context/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Layout from "@/components/Layout";
import AIControlCenter from "@/pages/AIControlCenter";

const root = ReactDOM.createRoot(document.getElementById("root"));
const isAIControlCenter = window.location.pathname === "/ai/control-center";

root.render(
  <React.StrictMode>
    {isAIControlCenter ? (
      <AuthProvider>
        <ProtectedRoute>
          <Layout>
            <AIControlCenter />
          </Layout>
        </ProtectedRoute>
      </AuthProvider>
    ) : (
      <App />
    )}
  </React.StrictMode>,
);
