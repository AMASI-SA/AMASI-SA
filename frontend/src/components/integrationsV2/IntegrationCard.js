/**
 * Compatibility entrypoint for extensionless imports.
 *
 * React Scripts and Jest resolve .js before .jsx, so existing imports keep
 * their stable path while the protected legacy file remains untouched. The
 * focused V2 implementation can be validated independently and the shim can
 * be removed after the historical Ads Manager workflow guard is repaired.
 */
import IntegrationCardV2 from "./IntegrationCardV2";
import TikTokReportingSyncControl from "./TikTokReportingSyncControl";

export default function IntegrationCard(props) {
    const isTikTok = props.integration?.provider === "tiktok_ads";
    if (!isTikTok) return <IntegrationCardV2 {...props} />;
    return (
        <div className="space-y-2">
            <IntegrationCardV2 {...props} />
            <TikTokReportingSyncControl integration={props.integration} />
        </div>
    );
}
