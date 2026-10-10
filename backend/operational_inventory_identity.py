"""Stable operational invoice-line identity, including canonical product variants."""


def line_identity(line):
    # Historical invoices have no variant; never assign their stock to a color.
    return (line['kind'], line.get('item_id', line.get('id')), line.get('variant_id') or None)


def variant_fields(line):
    return {'variant_id': line['variant_id']} if line.get('variant_id') else {}


def compatible_payload(payload):
    # Pydantic inserts optional defaults. Remove only empty new metadata so
    # historical invoice and adjustment request fingerprints remain unchanged.
    return {**payload, 'lines':[
        {k:v for k,v in line.items() if not (k == 'variant_id' and v is None)
         and not (k == 'personalizations' and v == [])}
        for line in payload.get('lines', [])]}
