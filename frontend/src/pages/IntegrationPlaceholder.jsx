/**
 * Generic "Coming Soon" placeholder for Integration pages whose
 * backend work is queued for Day 4/5 or later.
 *
 * Keeps the Navigation skeleton honest so we never have to refactor
 * the sidebar IA later. Each placeholder explicitly states which
 * phase it ships in.
 */
import { Link } from "react-router-dom";
import SallaWebhookMonitor from "./SallaWebhookMonitor";

export default function IntegrationPlaceholder({
  title, subtitle, phase = "Day 4-5", icon = "🚧",
  testid = "integration-placeholder",
  related = [],
}) {
  if (testid === "salla-events-placeholder") {
    return <SallaWebhookMonitor />;
  }

  return (
    <div className="max-w-4xl mx-auto p-6 md:p-10" dir="rtl" data-testid={testid}>
      <div className="rounded-2xl border-2 border-dashed border-slate-300 bg-white p-10 text-center">
        <div className="text-5xl mb-3">{icon}</div>
        <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900">
          {title}
        </h1>
        {subtitle && (
          <p className="text-sm text-slate-500 mt-2 max-w-xl mx-auto">
            {subtitle}
          </p>
        )}
        <div className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-amber-50 border border-amber-200 text-amber-800 text-xs font-bold">
          <span>قيد التنفيذ — {phase}</span>
        </div>
        {related.length > 0 && (
          <div className="mt-8">
            <p className="text-xs text-slate-500 mb-2">صفحات ذات صلة جاهزة الآن:</p>
            <div className="flex flex-wrap gap-2 justify-center">
              {related.map((r) => (
                <Link
                  key={r.to}
                  to={r.to}
                  className="px-3 py-1.5 rounded-lg bg-blue-50 border border-blue-200 text-blue-700 text-xs font-bold hover:bg-blue-100"
                  data-testid={`related-${r.to.replace(/\//g, '-')}`}
                >
                  {r.label} →
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
