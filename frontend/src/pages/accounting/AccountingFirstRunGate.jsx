import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { getOnboardingSession, listOnboardingSessions } from "../../services/accountingOnboarding";

// Read only. Wizard completion is independent of handoff, posting and activation.
export async function accountingFirstRunPage() {
    try {
        const listing = await listOnboardingSessions();
        for (const item of listing.items || []) {
            if (!["reviewed", "handed_off"].includes(item.status) || !item.id) continue;
            const session = await getOnboardingSession(item.id);
            if (["reviewed", "handed_off"].includes(session.status)
                && session.reviewed_by && session.reviewed_at && session.reviewed_hash
                && session.preview?.hash === session.reviewed_hash) return "home";
        }
    } catch {
        // Unavailable or malformed backend evidence never implies completion.
    }
    return "opening-balances";
}

export default function AccountingFirstRunGate() {
    const [, setSearchParams] = useSearchParams();
    useEffect(() => {
        let active = true;
        accountingFirstRunPage().then(page => {
            if (active) setSearchParams({ workspace: "financial", page }, { replace: true });
        });
        return () => { active = false; };
    }, [setSearchParams]);
    return <div role="status" dir="rtl">جاري التحقق من مرحلة التأسيس المحاسبي…</div>;
}
