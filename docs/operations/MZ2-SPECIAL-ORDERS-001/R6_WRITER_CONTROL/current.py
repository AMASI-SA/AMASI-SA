#!/usr/bin/env python3
"""Read-only current source contract: immutable R5 + R6 + explicit R6.1 layer."""
from pathlib import Path
import importlib.util
import json
import re

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def check():
    spec=importlib.util.spec_from_file_location('r6_original_contract',HERE/'verify.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _,expected=module.inputs()
    change=module.parse(module.regular(HERE/'hardening.json'))
    paths={'backend/mezan_special_orders/binding.py',
           'backend/mezan_special_orders/tests/test_write_control_bulk_safety.py'}
    if (change.get('base_sha')!='fa54ae174f240ecb714b20bfb64bfc081de5f14b'
            or change.get('version')!='R6.1_BULK_WRITE_GUARD' or set(change.get('files',{}))!=paths):
        raise ValueError('unexpected_hardening_contract')
    for name,row in change['files'].items():
        if row['old_sha256']!=expected.get(name) or not re.fullmatch(r'[0-9a-f]{64}',row['new_sha256']):
            raise ValueError('broken_source_chain')
        expected[name]=row['new_sha256']
    for name,fingerprint in expected.items():
        if module.sha(module.regular(ROOT/name))!=fingerprint:
            raise ValueError('current_source_mismatch:'+name)
    return {'status':'R6_1_CURRENT_SOURCE_VERIFIED','verified_paths':len(expected),
            'historical_archives_unchanged':True,'writes_performed':False,
            'deployment_authorized':False}


if __name__=='__main__':
    try:
        output=check();code=0
    except (ValueError,OSError):
        output={'status':'BLOCKED_CURRENT_SOURCE','deployment_authorized':False};code=2
    print(json.dumps(output,sort_keys=True))
    raise SystemExit(code)
