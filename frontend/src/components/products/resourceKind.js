export function resourceKind(resource) {
    if (resource?.kind === "service") return "service";
    if (["stock_component", "component"].includes(resource?.kind)) return "component";
    return resource?.track_inventory === true ? "component" : "service";
}
