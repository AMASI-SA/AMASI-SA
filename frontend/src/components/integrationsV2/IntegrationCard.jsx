import { useState } from "react";
import { toast } from "sonner";
import { startMetaConnection } from "../../services/metaIntegrationsV2";
import {
    executeIntegrationReconnect,
    integrationReconnectEnabled,
} from "../../lib/integrationReconnect";
import IntegrationCardV2 from "./IntegrationCardV2";

function metaReconnectErrorMessage(error) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (typeof detail?.message === "string" && detail.message.trim()) {
        return detail.message;
    }
    if (error?.response?.status === 403) {
        return "إعادة ربط Meta متاحة لمالك الحساب فقط.";
    }
    if (detail?.code === "meta_oauth_not_configured") {
        return "إعدادات Meta OAuth في Backend غير مكتملة.";
    }
    if (detail?.code === "meta_oauth_frontend_url_missing") {
        return "عنوان واجهة ميزان غير مضبوط في Backend.";
    }
    return "تعذر بدء إعادة ربط Meta. حدّث الصفحة ثم حاول مجددًا.";
}

export default function IntegrationCard(props) {
    const {
        integration,
        settingsAvailable = false,
        onSettings,
    } = props;
    const [reconnecting, setReconnecting] = useState(false);
    const metaReconnectCommand = integration?.provider === "meta_ads"
        && integrationReconnectEnabled(integration);
    const effectiveSettingsAvailable = settingsAvailable || metaReconnectCommand;

    async function handleSettings(provider) {
        if (provider !== "meta_ads" || !metaReconnectCommand) {
            onSettings?.(provider);
            return;
        }
        if (reconnecting) return;

        setReconnecting(true);
        try {
            await executeIntegrationReconnect({
                integration,
                startMetaConnection,
                assignLocation: (authorizationUrl) => {
                    window.location.assign(authorizationUrl);
                },
            });
        } catch (error) {
            toast.error(metaReconnectErrorMessage(error));
            setReconnecting(false);
        }
    }

    return (
        <IntegrationCardV2
            {...props}
            settingsAvailable={effectiveSettingsAvailable && !reconnecting}
            onSettings={handleSettings}
        />
    );
}
