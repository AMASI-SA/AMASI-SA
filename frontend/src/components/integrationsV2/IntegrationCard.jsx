import AdvertisingAccountCard from "./AdvertisingAccountCard";
import IntegrationCardV2 from "./IntegrationCardV2";

function isAdvertisingAccountsWorkspace() {
    if (typeof window === "undefined") return false;
    return new URLSearchParams(window.location.search).get("workspace") === "accounts";
}

export default function IntegrationCard({ compactAdvertisingAccounts = false, ...props }) {
    if (compactAdvertisingAccounts || isAdvertisingAccountsWorkspace()) {
        return <AdvertisingAccountCard {...props} />;
    }
    return <IntegrationCardV2 {...props} />;
}
