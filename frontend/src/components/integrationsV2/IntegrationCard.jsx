import AdvertisingAccountCard from "./AdvertisingAccountCard";
import IntegrationCardV2 from "./IntegrationCardV2";

function isAdvertisingAccountsWorkspace() {
    if (typeof window === "undefined") return false;
    if (window.location.pathname !== "/integrations-v2") return false;
    return new URLSearchParams(window.location.search).get("workspace") === "accounts";
}

export default function IntegrationCard(props) {
    if (isAdvertisingAccountsWorkspace()) {
        return <AdvertisingAccountCard {...props} />;
    }
    return <IntegrationCardV2 {...props} />;
}
