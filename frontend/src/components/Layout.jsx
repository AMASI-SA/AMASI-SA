import Sidebar from "./Sidebar";
import { Toaster } from "../components/ui/sonner";

export default function Layout({ children }) {
    return (
        <div className="min-h-screen bg-background grain">
            <Sidebar />
            <main className="ps-64 min-h-screen" data-testid="main-content">
                <div className="max-w-7xl mx-auto px-6 lg:px-10 py-8">{children}</div>
            </main>
            <Toaster richColors position="top-center" />
        </div>
    );
}
