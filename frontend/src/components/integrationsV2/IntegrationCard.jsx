/**
 * Compatibility entrypoint for explicit .jsx imports.
 *
 * Production and test environments may resolve extensionless imports to
 * either IntegrationCard.js or IntegrationCard.jsx. Keep both entrypoints
 * behaviorally identical so Meta's account-selection and reporting controls
 * are always rendered.
 */
import IntegrationCardV2 from "./IntegrationCardV2";
import MetaReportingControl from "./MetaReportingControl";

export default function IntegrationCard(props) {
    const isMeta = props.integration?.provider === "meta_ads";
    if (!isMeta) return <IntegrationCardV2 {...props} />;
    return (
        <div className="space-y-2">
            <IntegrationCardV2 {...props} />
            <MetaReportingControl integration={props.integration} />
        </div>
    );
}
