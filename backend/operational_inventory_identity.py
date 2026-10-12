"""Stable operational invoice-line identity, including canonical product variants."""


def line_identity(line):
    # Historical invoices have no variant; never assign their stock to a color.
    base = (line['kind'], line.get('item_id', line.get('id')), line.get('variant_id') or None)
    return (*base, line['purchase_line_key']) if line.get('purchase_line_key') else base


def variant_fields(line):
    result = {'variant_id': line['variant_id']} if line.get('variant_id') else {}
    if line.get('purchase_line_key'):result['purchase_line_key'] = line['purchase_line_key']
    return result


def compatible_payload(payload):
    # Pydantic inserts optional defaults. Remove only empty new metadata so
    # historical invoice and adjustment request fingerprints remain unchanged.
    return {**payload, 'lines':[
        {k:v for k,v in line.items() if not (k == 'variant_id' and v is None)
         and not (k == 'personalizations' and v == [])
         and not (k in ('purchase_configuration','location_id','purchase_line_key') and v is None)}
        for line in payload.get('lines', [])]}
