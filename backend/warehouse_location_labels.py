"""Human location labels for opt-in warehouses; scanner identities stay stable."""

def location_label_fields(warehouse, cabinet_number, row):
    if warehouse.get("location_numbering") != "cabinet-row-slot":
        return {}
    return {"display_code": f"{cabinet_number}-{row['row']}-{row['column']}",
            "location_numbering": "cabinet-row-slot", "cabinet_number": cabinet_number}
