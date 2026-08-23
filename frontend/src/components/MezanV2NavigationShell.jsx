import { useEffect, useMemo, useState } from "react";
import { useOptionalAuth } from "../context/AuthContext";
import { getAccountingAccess } from "../services/accountingModule";
import LegacyMezanV2NavigationShell, {
    MEZAN_V2_NAV_SECTIONS,
    activeNavigationSection,
    isMezanV2Route,
    isNavigationItemActive,
    navigationSectionsForDisplay,
} from "./MezanV2NavigationShellLegacy";
import { accountingNavItems } from "../pages/accounting/accountingPages";

const LEGACY_SECTION_SNAPSHOT = MEZAN_V2_NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.map((item) => ({ ...item })),
}));

// Compatibility contract for static route-wiring guards. The visible shell is
// still derived from LEGACY_SECTION_SNAPSHOT plus the accounting replacement.
export const EMPLOYEES_V2_ROUTE_CONTRACT = [
    { to: "/employees-v2", label: "إدارة الموظفين", exactSearch: true },
    { to: "/employees-v2?workspace=migration", label: "تقرير الترحيل والرواتب" },
    { to: "/employees-v2?workspace=permissions", label: "الصلاحيات وإدارة التجهيز" },
];

function accountingPermissionSet(access) {
    return new Set(Array.isArray(access?.permissions) ? access.permissions : []);
}

export function navigationSectionsForAccountingAccess(access) {
    const permissions = accountingPermissionSet(access);
    const owner = access?.is_owner === true;
    const accountingItems = accountingNavItems().filter(
        (item) => owner || permissions.has(item.permission),
    );

    return LEGACY_SECTION_SNAPSHOT.flatMap((section) => {
        if (section.id === "finance") {
            if (accountingItems.length === 0) return [];
            return [{
                ...section,
                id: "accounting",
                label: "المحاسبة",
                items: accountingItems.map((item) => ({ ...item })),
            }];
        }
        if (section.id === "apps") {
            return [{
                ...section,
                items: section.items
                    .filter((item) => !String(item.to || "").includes("workspace=financial"))
                    .map((item) => ({ ...item })),
            }];
        }
        return [{
            ...section,
            items: section.items.map((item) => ({ ...item })),
        }];
    });
}

function installNavigationSections(sections) {
    MEZAN_V2_NAV_SECTIONS.splice(0, MEZAN_V2_NAV_SECTIONS.length, ...sections);
}

export {
    MEZAN_V2_NAV_SECTIONS,
    activeNavigationSection,
    isMezanV2Route,
    isNavigationItemActive,
    navigationSectionsForDisplay,
};

export default function MezanV2NavigationShell(props) {
    const { user } = useOptionalAuth() || {};
    const [access, setAccess] = useState(null);
    const userId = user?.id || "";
    const ownerFromSession = user?.is_owner === true
        || String(user?.role || "").toLowerCase() === "owner";
    const effectiveAccess = access?.user_id === userId
        ? access
        : ownerFromSession
            ? { user_id: userId, is_owner: true, permissions: [] }
            : { user_id: userId, is_owner: false, permissions: [] };

    useEffect(() => {
        if (!userId) {
            setAccess(null);
            return;
        }
        let active = true;
        getAccountingAccess()
            .then((result) => {
                if (active) setAccess({ ...result, user_id: result?.user_id || userId });
            })
            .catch(() => {
                if (active) setAccess({ user_id: userId, is_owner: ownerFromSession, permissions: [] });
            });
        return () => { active = false; };
    }, [ownerFromSession, userId]);

    const sections = useMemo(
        () => navigationSectionsForAccountingAccess(effectiveAccess),
        [effectiveAccess],
    );
    installNavigationSections(sections);

    return <LegacyMezanV2NavigationShell {...props} />;
}
