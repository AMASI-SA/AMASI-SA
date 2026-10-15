import AdvertisingAccountCard from "./AdvertisingAccountCard";
import FullIntegrationCard from "./IntegrationCardV2.jsx";

function isAdvertisingAccountsWorkspace() {
    if (typeof window === "undefined") return false;
    return new URLSearchParams(window.location.search).get("workspace") === "accounts";
}

export default function IntegrationCardV2({ compactAdvertisingAccounts = false, ...props }) {
    if (compactAdvertisingAccounts || isAdvertisingAccountsWorkspace()) {
        return <AdvertisingAccountCard {...props} />;
    }
    return <FullIntegrationCard {...props} />;
}
