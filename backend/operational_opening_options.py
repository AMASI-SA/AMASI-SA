"""Read cached product options without inventing identities or changing storage."""
from copy import deepcopy


def opening_option_source(row):
    # Explicit canonical definitions, including an empty list, always win.
    if 'options' in row:
        return row
    cached = row.get('raw_salla')
    source = cached.get('options') if isinstance(cached, dict) else None
    if source is None or source == []:
        return row
    invalid = {**row, 'options': None}
    if not isinstance(source, list):
        return invalid
    result = []
    for option in source:
        if not isinstance(option, dict):
            return invalid
        oid = option.get('id')
        name = option.get('name') or option.get('title') or option.get('label')
        required = option.get('required', option.get('is_required'))
        kind = option.get('type')
        if type(oid) not in (str, int) or not str(oid).strip() or not isinstance(name, str) or not name.strip() or type(required) is not bool or not isinstance(kind, str):
            return invalid
        values = option.get('values', option.get('options', []))
        if not isinstance(values, list):
            return invalid
        normalized = []
        for value in values:
            if not isinstance(value, dict):
                return invalid
            vid = value.get('id')
            label = value.get('name') or value.get('title') or value.get('label')
            if type(vid) not in (str, int) or not str(vid).strip() or not isinstance(label, str) or not label.strip():
                return invalid
            normalized.append({**deepcopy(value), 'id': str(vid), 'name': label})
        result.append({'id': str(oid), 'name': name, 'required': required, 'type': kind, 'values': normalized})
    return {**row, 'options': result}
