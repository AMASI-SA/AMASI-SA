import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useOptionalAuth } from "../../context/AuthContext";
import { getAccountingAccess, getAccountingModuleStatus } from "../../services/accountingModule";
import AccountingCourierBankBindings from "./AccountingCourierBankBindings";
import AccountingHome from "./AccountingHome";
import AccountingPermissionsDialog from "./AccountingPermissionsDialog";
import AccountingSettlementRegister from "./AccountingSettlementRegister";
import AccountingSettlements from "./AccountingSettlements";
import {
    AccessDenied,
    AccountingHeader,
    LoadingBlock,
} from "./AccountingShared";
import { OpeningBalancesBlocked, PartialWorkflowPage } from "./AccountingWorkflowPages";
import { accountingPageFromSearchParams, userCanAccessAccounting } from "./accountingPages";

export default function AccountingWorkspace() {
    const { user } = useOptionalAuth() || {};
    const [searchParams] = useSearchParams();
    const page = accountingPageFromSearchParams(searchParams);
    const [access, setAccess] = useState(null);
    const [accessLoading, setAccessLoading] = useState(true);
    const [status, setStatus] = useState(null);
    const [statusLoading, setStatusLoading] = useState(true);
    const [permissionsOpen, setPermissionsOpen] = useState(false);
    const permissions = access?.permissions || [];
    const allowed = userCanAccessAccounting(user, page.permission, permissions);
    const needsStatus = page.id === "home" || page.id === "opening-balances";

    useEffect(() => {
        if (!user?.id) {
            setAccess(null);
            setAccessLoading(false);
            return;
        }
        let active = true;
        setAccessLoading(true);
        getAccountingAccess()
            .then((result) => { if (active) setAccess(result); })
            .catch(() => { if (active) setAccess({ is_owner: false, permissions: [] }); })
            .finally(() => { if (active) setAccessLoading(false); });
        return () => { active = false; };
    }, [user?.id]);

    useEffect(() => {
        if (accessLoading || !allowed || !needsStatus) {
            setStatusLoading(false);
            if (!needsStatus) setStatus(null);
            return;
        }
        let active = true;
        setStatusLoading(true);
        getAccountingModuleStatus(page.id)
            .then((result) => { if (active) setStatus(result); })
            .catch((error) => {
                if (!active || error?.response?.status === 403) return;
                const detail = error?.response?.data?.detail;
                toast.error(typeof detail === "string" ? detail : detail?.message || "تعذر تحميل حالة المحاسبة");
            })
            .finally(() => { if (active) setStatusLoading(false); });
        return () => { active = false; };
    }, [accessLoading, allowed, needsStatus, page.id]);

    if (accessLoading) return <LoadingBlock label="جاري التحقق من صلاحية المحاسبة…" />;
    if (!allowed) return <AccessDenied page={page} />;

    let content;
    if (page.id === "home") {
        content = statusLoading
            ? <LoadingBlock />
            : <AccountingHome status={status} user={user} accountingPermissions={permissions} />;
    } else if (page.id === "settlements") {
        content = (
            <div className="space-y-5">
                <AccountingSettlements accountingPermissions={permissions} />
                <AccountingSettlementRegister accountingPermissions={permissions} />
                <AccountingCourierBankBindings accountingPermissions={permissions} />
            </div>
        );
    } else if (page.id === "opening-balances") {
        content = statusLoading ? <LoadingBlock /> : <OpeningBalancesBlocked status={status} />;
    } else {
        content = <PartialWorkflowPage page={page} />;
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-workspace">
            <AccountingHeader page={page} canManagePermissions={access?.is_owner === true} onOpenPermissions={() => setPermissionsOpen(true)} />
            {content}
            <AccountingPermissionsDialog open={permissionsOpen} onClose={() => setPermissionsOpen(false)} />
        </div>
    );
}
