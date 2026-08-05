export function filterProductGroups(groups, { categoryId = "", kind = "service" } = {}) {
    if (!categoryId) return [];
    return (groups || []).filter((group) => (
        String(group.category_id || "") === String(categoryId)
        && String(group.group_kind || "") === String(kind)
    ));
}

export function groupNamesForResource(groups, groupIds) {
    const byId = new Map((groups || []).map((group) => [String(group.id), group]));
    return (groupIds || [])
        .map((groupId) => byId.get(String(groupId))?.name)
        .filter(Boolean);
}

export function resourceMatchesCategory(resource, categoryId) {
    if (!categoryId) return true;
    return (resource?.category_ids || []).map(String).includes(String(categoryId));
}

export function toggleSelectedGroup(selectedGroupIds, groupId) {
    const value = String(groupId);
    const current = (selectedGroupIds || []).map(String);
    return current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value];
}
