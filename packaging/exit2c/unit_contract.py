"""Strict unit acceptance. Expectations come from pre-creation synthetic source inputs."""
from collections import Counter
from acceptance_controller import check

GROUPS = ('ALLOCATIONS','PIECES','STATUS','EMPLOYEE','BATCH_LINK','SPECIFICATIONS','PROJECTED_OPTIONS')
METRICS = ('EXPECTED','ACTUAL','MISSING','DUPLICATE','UNEXPECTED','MISMATCH','MALFORMED')
CHECKS = ('UNIT_ALLOCATION_IDENTITIES','UNIT_PIECE_IDENTITIES','UNIT_ALLOCATION_STATUS',
          'UNIT_EMPLOYEE_ASSIGNMENT','UNIT_BATCH_LINK','UNIT_SPECIFICATIONS','UNIT_PROJECTED_OPTIONS')

def require(value):
    if not value: raise AssertionError('UNIT_CONTRACT_FAILED')

def unit_lines(records):
    if type(records) is not list or len(records)>64:
        return ['UNITS unavailable']
    result=[]
    for record in records:
        if (type(record) not in (tuple,list) or len(record)!=3 or
            type(record[0]) is not str or record[0] not in GROUPS or
            type(record[1]) is not str or record[1] not in METRICS or
            type(record[2]) is not int or not 0<=record[2]<=10000):
            return ['UNITS unavailable']
        result.append('UNITS '+record[0]+' '+record[1]+' '+str(record[2]))
    return result

def verify_units(expected, allocations, pieces, employee, batches, registries, files, *, state=None):
    def report(group, **counts):
        if state is not None:
            state.setdefault('_unit_evidence',[]).extend((group,key,value) for key,value in counts.items())
    def key(row):
        require(type(row) is dict and type(row.get('unit_index')) is int)
        require(type(row.get('order_number')) is str and type(row.get('order_item_id')) is str)
        return row['order_number'],row['order_item_id'],row['unit_index']
    wanted={(number,item,u) for (number,item),row in expected.items() for u in range(1,row['quantity']+1)}
    maps=[]
    for rows,group,identifier in ((allocations,'ALLOCATIONS',CHECKS[0]),(pieces,'PIECES',CHECKS[1])):
        with check(identifier):
            try:
                require(type(rows) is list and len(rows)<=10000)
                keys=[key(row) for row in rows]
            except Exception:
                report(group,MALFORMED=1)
                raise AssertionError('UNIT_STRUCTURE_FAILED') from None
            missing=len(wanted-set(keys)); duplicate=len(keys)-len(set(keys)); extra=len(set(keys)-wanted)
            report(group,EXPECTED=len(wanted),ACTUAL=len(keys),MISSING=missing,DUPLICATE=duplicate,UNEXPECTED=extra)
            require(not (missing or duplicate or extra))
            if group=='PIECES':
                ids=[row.get('id') for row in rows]
                malformed=sum(type(value) is not str or not value for value in ids)
                mismatch=sum(row.get('piece_id')!=row.get('id') for row in rows)
                duplicate_ids=len(ids)-len(set(value for value in ids if type(value) is str)) if not malformed else 0
                report(group,MALFORMED=malformed,MISMATCH=mismatch,DUPLICATE=duplicate_ids)
                require(not (malformed or mismatch or duplicate_ids))
            maps.append(dict(zip(keys,rows)))
    allocation_by_unit,piece_by_unit=maps
    with check(CHECKS[2]):
        wrong=sum(row.get('status')!='committed' for row in allocations)
        report('STATUS',MISMATCH=wrong);require(wrong==0)
    with check(CHECKS[3]):
        wrong=sum(row.get('responsible_employee_id')!=employee for row in pieces)
        wrong+=sum(row.get('responsible_employee_id')!=employee for row in registries)
        report('EMPLOYEE',MISMATCH=wrong);require(wrong==0)
    with check(CHECKS[4]):
        batch_ids=[row.get('id') for row in batches]
        registry_ids=[row.get('batch_id') for row in registries]
        file_ids=[row.get('batch_id') for row in files]
        require(all(type(x) is str and x for x in batch_ids+registry_ids+file_ids))
        duplicate=sum(len(ids)-len(set(ids)) for ids in (batch_ids,registry_ids,file_ids))
        report('BATCH_LINK',DUPLICATE=duplicate)
        require(duplicate==0 and set(batch_ids)==set(registry_ids)==set(file_ids))
        batch_by_id={row['id']:row for row in batches}
        registry_by_id={row['batch_id']:row for row in registries}
        file_by_id={row['batch_id']:row for row in files}
        line_by_unit={}
        for batch in batches:
            for line in batch['lines']:
                indices=line['unit_indices']
                require(type(indices) is list and all(type(i) is int for i in indices))
                require(type(line['quantity']) is int and line['quantity']==len(indices))
                for index in indices:
                    k=(line['order_number'],line['order_item_id'],index)
                    require(k not in line_by_unit)
                    line_by_unit[k]=(batch['id'],line)
        require(set(line_by_unit)==wanted)
        mismatch=0
        for k,piece in piece_by_unit.items():
            bid,line=line_by_unit[k]
            registry=registry_by_id[bid];file=file_by_id[bid]
            mismatch+=int(not (
                allocation_by_unit[k].get('batch_id')==piece.get('batch_id')==bid and
                piece.get('file_number')==registry.get('file_number')==file.get('file_number') and
                bool(file.get('file_number')) and
                piece.get('product_id')==line.get('product_id')==expected[k[:2]]['product_id'] and
                piece.get('group_key')==line.get('group_key')==allocation_by_unit[k].get('group_key') and
                bool(line.get('group_key')) and
                piece.get('user_id')==registry.get('user_id')==batch_by_id[bid].get('user_id')==allocation_by_unit[k].get('user_id') and
                bool(piece.get('user_id'))))
        report('BATCH_LINK',MISMATCH=mismatch); require(mismatch==0)
    with check(CHECKS[5]):
        errors=Counter()
        for k,piece in piece_by_unit.items():
            options=expected[k[:2]]['options']
            _,line=line_by_unit[k]
            for fields in (piece.get('specifications_snapshot'),line.get('file_spec_fields')):
                if type(fields) is not list or any(type(f) is not dict for f in fields):
                    errors['MALFORMED']+=1;continue
                names=[f.get('name') for f in fields]
                if any(type(n) is not str for n in names):
                    errors['MALFORMED']+=1;continue
                errors['MISSING']+=len(set(options)-set(names))
                errors['UNEXPECTED']+=len(set(names)-set(options))
                errors['DUPLICATE']+=len(names)-len(set(names))
                for field in fields:
                    if field.get('name') in options:
                        errors['MISMATCH']+=int(field.get('value')!=options[field['name']])
                        name=field['name']
                        errors['MISMATCH']+=int(field.get('spec_key')!=('color' if name=='اللون' else name))
                        errors['MISMATCH']+=int(field.get('text')!=name+': '+options[name])
            errors['MISMATCH']+=int(piece.get('specifications_snapshot')!=line.get('file_spec_fields'))
            errors['MISMATCH']+=int(line.get('color')!=options['اللون'])
        report('SPECIFICATIONS',**{m:errors[m] for m in ('MISSING','DUPLICATE','UNEXPECTED','MISMATCH','MALFORMED')})
        require(not any(errors.values()))
    with check(CHECKS[6]):
        mismatch=0
        for k,piece in piece_by_unit.items():
            # Declared fixture has exactly color + engraving. Application projects color separately.
            options=expected[k[:2]]['options']
            require(set(options)=={'اللون','النقش'})
            extras={'النقش':options['النقش']}
            _,line=line_by_unit[k]
            mismatch+=int(piece.get('product_options_snapshot')!=extras)
            mismatch+=int(line.get('product_options')!=extras)
        report('PROJECTED_OPTIONS',MISMATCH=mismatch);require(mismatch==0)
