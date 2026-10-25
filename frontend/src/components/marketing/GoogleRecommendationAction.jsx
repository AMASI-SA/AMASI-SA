import { useEffect, useRef, useState } from "react";
import { googleErrorMessage, googleRecommendationAction, isGoogleRequestCancelled, previewGoogleRecommendation } from "../../services/googleAds";

export default function GoogleRecommendationAction({ analysisId, recommendation, onPreview }) {
    const [busy, setBusy] = useState(false), [error, setError] = useState("");
    const active = useRef(false), controller = useRef(null);
    const recommendationId = recommendation?.recommendation_id;
    useEffect(() => {
        active.current = true; setBusy(false); setError("");
        return () => { active.current = false; controller.current?.abort(); controller.current = null; };
    }, [analysisId, recommendationId]);
    if (!analysisId || !recommendationId || !googleRecommendationAction(recommendation) || !onPreview) return null;
    async function review() {
        if (controller.current) return;
        setBusy(true); setError("");
        const request = new AbortController(); controller.current = request;
        try {
            const proposal = await previewGoogleRecommendation(analysisId, recommendationId, { signal: request.signal });
            if (active.current && !request.signal.aborted) onPreview(proposal);
        } catch (failure) {
            if (active.current && !request.signal.aborted && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر تجهيز معاينة التوصية. حدّث تحليل الحملة."));
        } finally {
            if (controller.current === request) controller.current = null;
            if (active.current && !request.signal.aborted) setBusy(false);
        }
    }
    return <div className="space-y-2">
        <button type="button" className="rounded-xl border border-violet-200 bg-white px-4 py-2.5 text-sm font-bold disabled:opacity-40" disabled={busy} onClick={review}>{busy ? "جارٍ تجهيز معاينة التوصية…" : "مراجعة تنفيذ التوصية"}</button>
        {error && <p role="alert" className="text-xs leading-6 text-rose-800">{error}</p>}
    </div>;
}
