"""Purchase description metadata, never a physical warehouse receipt or writer."""
from copy import deepcopy
from collections import Counter
import unicodedata
from operational_balance_store import digest, fail
from operational_balance_sources import rows, usable

TEXT={'text','string','textarea'}
CHOICE={'select','radio','color','image'}

async def locations(db,owner):
    warehouses=await db.warehouse_locations_warehouses.find({'user_id':owner,'status':{'$ne':'disabled'}},{'_id':0}).to_list(20001)
    stored=await db.warehouse_locations.find({'user_id':owner,'state':{'$ne':'disabled'}},{'_id':0}).to_list(20001)
    if len(warehouses)>20000 or len(stored)>20000:fail('inventory_locations_incomplete','تعذر قراءة المواقع كاملة',409)
    if any(n>1 for n in Counter(w.get('id') for w in warehouses).values()) or any(n>1 for n in Counter(l.get('id') for l in stored).values()):fail('inventory_locations_ambiguous','هوية الموقع غير محسومة في ميزان 2',409)
    by_id={w['id']:w for w in warehouses if w.get('id')}
    return [{'id':l['id'],'name':str(l.get('name') or l.get('code') or l['id']),'code':str(l.get('code') or l['id']),'warehouse_id':l['warehouse_id'],'warehouse_name':str(by_id[l['warehouse_id']].get('name') or l['warehouse_id'])} for l in stored if l.get('id') and l.get('warehouse_id') in by_id]


def option_contract(row):
    source=row.get('options',[]);result=[];issues=[];seen=set()
    if not isinstance(source,list):return [],['خيارات المنتج غير مكتملة في ميزان 2']
    count=row.get('options_count',len(source))
    if type(count) is not int or count<0 or count>len(source):issues.append('خيارات المنتج غير مكتملة في ميزان 2')
    for option in source:
        if not isinstance(option,dict) or not isinstance(option.get('id'),str) or not option['id'].strip() or option['id'] in seen or not isinstance(option.get('name'),str) or not option['name'].strip() or type(option.get('required')) is not bool or not isinstance(option.get('type'),str):
            issues.append('هوية أو نوع خيار المنتج غير مكتمل');continue
        seen.add(option['id']);kind=option['type'];values=[];ids=set()
        if kind in CHOICE:
            choices=option.get('values',[])
            if not isinstance(choices,list) or not choices:issues.append('قيم الخيار '+option['name']+' غير مكتملة');choices=[]
            for value in choices:
                if not isinstance(value,dict) or not isinstance(value.get('id'),str) or not value['id'] or value['id'] in ids or not isinstance(value.get('name'),str) or not value['name']:
                    issues.append('هوية قيمة الخيار غير مكتملة');continue
                ids.add(value['id'])
                from operational_balance_inventory import image_url
                values.append({'id':value['id'],'name':value['name'],'image_url':image_url(value.get('image'))})
        elif kind not in TEXT:
            issues.append('رفع صورة تخصيص غير مدعوم حاليًا' if kind=='file' else 'نوع الخيار '+option['name']+' غير مدعوم حاليًا')
        result.append({'id':option['id'],'name':option['name'],'type':kind,'required':option['required'],'values':values})
    return result,issues


def combinations(row,options):
    variants=row.get('variants') or [];result=[]
    if not isinstance(variants,list) or not variants or row.get('variants_count',len(variants))!=len(variants):return []
    for variant in variants:
        selections=variant.get('selections') if isinstance(variant,dict) else None
        if not isinstance(selections,list) or not selections:return []
        values=[]
        for selected in selections:
            if not isinstance(selected,dict):return []
            field=next((f for f in options if f['id']==selected.get('option_id') or f['name']==selected.get('name')),None)
            choice=next((v for v in (field or {}).get('values',[]) if v['id']==selected.get('value_id') or v['name']==selected.get('value')),None)
            if not field or not choice:return []
            values.append({'option_id':field['id'],'value_id':choice['id']})
        result.append(values)
    return result


async def products(db,owner):
    from operational_balance_inventory import image_url
    result=[];seen=set()
    for row in await rows(db,owner,'mezan_products_v2'):
        if not usable(row) or row.get('archived') or row.get('deleted_at'):continue
        identity=row.get('mezan_product_id')
        if not isinstance(identity,str) or not identity or identity in seen or not row.get('name'):fail('inventory_catalog_ambiguous','هوية المنتج غير مكتملة')
        seen.add(identity);options,issues=option_contract(row)
        result.append({'id':identity,'kind':'product','name':row['name'],'image_url':image_url(row.get('main_image')),'sku':row.get('sku') or '', 'unit':row.get('unit') or 'piece','options':options,'configuration_issues':issues,'selection_combinations':combinations(row,options),'conditional_rules_verified':False})
    return result


def configuration(value,product):
    if not isinstance(value,dict) or set(value)!={'state','selections','inputs'} or value['state'] not in ('raw','ready') or not isinstance(value['selections'],list) or not isinstance(value['inputs'],list) or len(value['selections'])+len(value['inputs'])>100:fail('inventory_purchase_configuration_invalid','بيانات حالة المنتج وخياراته غير صالحة',422)
    fields={f['id']:f for f in product.get('options',[])};seen=set();selected=[];inputs=[];labels=[]
    for group in ('selections','inputs'):
        for entry in value[group]:
            expected={'option_id','value_id'} if group=='selections' else {'option_id','value'}
            if not isinstance(entry,dict) or set(entry)!=expected or not isinstance(entry['option_id'],str):fail('inventory_purchase_configuration_invalid','بيانات الخيار غير صالحة',422)
            field=fields.get(entry['option_id'])
            if not field or field['id'] in seen:fail('inventory_purchase_option_invalid','خيار غير معتمد أو مكرر',422)
            seen.add(field['id'])
            if group=='selections':
                choice=next((v for v in field['values'] if v['id']==entry['value_id']),None)
                if field['type'] not in CHOICE or not choice:fail('inventory_purchase_option_invalid','قيمة الخيار غير معتمدة',422)
                selected.append({'option_id':field['id'],'value_id':choice['id']});labels.append({'option_id':field['id'],'name':field['name'],'type':field['type'],'value_id':choice['id'],'value':choice['name']})
            else:
                text=entry['value']
                if field['type'] not in TEXT or not isinstance(text,str) or any(unicodedata.category(c).startswith('C') and not c.isspace() for c in text):fail('inventory_purchase_option_invalid','قيمة التخصيص غير صالحة',422)
                text=' '.join(unicodedata.normalize('NFC',text).split())
                if not text or len(text)>(1000 if field['type']=='textarea' else 100):fail('inventory_purchase_option_invalid','قيمة التخصيص فارغة أو طويلة',422)
                inputs.append({'option_id':field['id'],'value':text});labels.append({'option_id':field['id'],'name':field['name'],'type':field['type'],'value':text})
    maps=product.get('selection_combinations',[])
    represented={v['option_id'] for choice in maps for v in choice}
    comparable=[v for v in selected if v['option_id'] in represented]
    if comparable and not any(all(v in choice for v in comparable) for choice in maps):fail('inventory_purchase_combination_invalid','اللون والمقاس لا يطابقان تركيبة معتمدة في ميزان 2',422)
    if value['state']=='raw' and inputs:fail('inventory_purchase_raw_customization','المنتج الخام لا يحمل تخصيصًا جاهزًا',422)
    if value['state']=='ready':
        if any(f['required'] and f['id'] not in seen for f in fields.values()):fail('inventory_purchase_required_option','أكمل خيارات المنتج الجاهز المطلوبة',422)
        # A missing canonical field cannot be silently treated as optional.
        if any('غير مكتمل' in issue for issue in product.get('configuration_issues',[])):fail('inventory_purchase_configuration_incomplete','خيارات المنتج الجاهز غير مكتملة في ميزان 2',422)
    if product['kind']=='component' and seen:fail('inventory_purchase_component_configuration','المكون يسجل خامًا دون تخصيصات المنتج',422)
    result={'state':value['state'],'selections':sorted(selected,key=lambda x:x['option_id']),'inputs':sorted(inputs,key=lambda x:x['option_id'])}
    for label in labels:
        label['option_name']=label['name']
        if 'value_id' in label:label['value_name']=label['value']
    return result,labels


async def describe(db,owner,line,base_products,location_rows):
    if line.get('variant_id'):fail('inventory_purchase_mixed_variant','اختر خيارات المنتج دون خلطها بهوية تركيبة أخرى',422)
    if line.get('personalizations'):fail('inventory_purchase_mixed_customization','استخدم خيارات المنتج دون توزيع الأسماء القديم',422)
    product=base_products.get((line['kind'],line['item_id']))
    if not product:fail('inventory_item_not_mz2','المنتج أو المكون غير متاح في ميزان 2',422)
    location=next((l for l in location_rows if l['id']==line.get('location_id')),None)
    if not location:fail('inventory_purchase_location_invalid','اختر موقعًا معتمدًا في ميزان 2',422)
    config,labels=configuration(line['purchase_configuration'],product)
    return {**product,'purchase_configuration':config,'purchase_option_labels':labels,'location_id':location['id'],'location':deepcopy(location),'location_name':location['name'],'purchase_line_key':digest(['operational-purchase-description',line['kind'],line['item_id'],config,location['id']]),'physical_stock_status':'unproven'}
