export const WEB_PERMISSIONS_PATH = "/employees-v2?workspace=permissions";

export function managedWebPages(user) {
    return Array.isArray(user?.web_page_access?.pages) ? user.web_page_access.pages : null;
}

export function grantedPageAt(pages, location) {
    const pathname = (location.pathname || "/").replace(/\/+$/, "") || "/";
    const requested = new URLSearchParams(location.search);
    return pages?.find((page) => {
        const [target, query] = page.to.split("?");
        if (target !== pathname) return false;
        const routeParams = new URLSearchParams(query);
        return ["workspace", "stage", "tab", ...(routeParams.has("page") ? ["page"] : [])].every((key) => {
            const actual = requested.getAll(key);
            return routeParams.has(key)
                ? actual.length === 1 && actual[0] === routeParams.get(key)
                : actual.length === 0;
        });
    }) || null;
}
