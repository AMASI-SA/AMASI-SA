export default function AuthLoadingScreen() {
    return (
        <main
            className="min-h-screen flex items-center justify-center bg-background"
            data-testid="auth-loading"
            dir="rtl"
            aria-live="polite"
        >
            <div className="text-brand text-lg font-medium">جاري التحقق…</div>
        </main>
    );
}
