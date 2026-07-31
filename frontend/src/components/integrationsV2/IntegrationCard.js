/**
 * Compatibility entrypoint for extensionless imports.
 *
 * React Scripts and Jest resolve .js before .jsx, so existing imports keep
 * their stable path while the protected legacy file remains untouched. The
 * focused V2 implementation can be validated independently and the shim can
 * be removed after the historical Ads Manager workflow guard is repaired.
 */
import IntegrationCardV2 from "./IntegrationCardV2";
import MetaReportingControl from "./MetaReportingControl";
import TikTokReportingSyncControl from "./TikTokReportingSyncControl";

export default function IntegrationCard(props) {
    const provider = props.integration?.provider;
    const extra = provider === "tiktok_ads"
        ? <TikTokReportingSyncControl integration={props.integration} />
        : provider === "meta_ads"
            ? <MetaReportingControl integration={props.integration} />
            : null;
    if (!extra) return <IntegrationCardV2 {...props} />;
    return (
        <div className="space-y-2">
            <IntegrationCardV2 {...props} />
            {extra}
        </div>
    );
}
