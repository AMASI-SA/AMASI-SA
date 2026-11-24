"""Human location labels; the stored scanner and QR identities stay stable."""
from typing import Literal


LocationNumbering = Literal["legacy", "cabinet-row-slot", "cabinet-column-row"]


def location_label_fields(warehouse, cabinet_number, row):
    mode = warehouse.get("location_numbering")
    if mode == "cabinet-column-row":
        coordinates = (cabinet_number, row["column"], row["row"])
        separator = "" if all(value < 10 for value in coordinates) else "-"
        display_code = separator.join(str(value) for value in coordinates)
    elif mode == "cabinet-row-slot":
        # Preserve the meaning of labels already used by the older opt-in mode.
        display_code = f"{cabinet_number}-{row['row']}-{row['column']}"
    else:
        return {}
    return {"display_code": display_code, "location_numbering": mode,
            "cabinet_number": cabinet_number}
