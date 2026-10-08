"""Stable operational invoice-line identity, including canonical product variants."""


def line_identity(line):
    # Historical invoices have no variant; never assign their stock to a color.
    return (line['kind'], line.get('item_id', line.get('id')), line.get('variant_id') or None)


def variant_fields(line):
    return {'variant_id': line['variant_id']} if line.get('variant_id') else {}


def compatible_payload(payload):
    # Pydantic inserts the new optional field as None. Excluding only this field
    # preserves fingerprints of in-flight commands created before this change.
    return {**payload, 'lines':[
        {k:v for k,v in line.items() if k != 'variant_id' or v is not None}
        for line in payload.get('lines', [])]}
