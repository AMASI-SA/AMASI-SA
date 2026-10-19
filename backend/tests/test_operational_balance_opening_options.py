import unittest
from copy import deepcopy
from operational_opening_options import opening_option_source

class CachedOpeningOptions(unittest.TestCase):
    def setUp(self):
        self.row = {'options_count': 2, 'raw_salla': {'options': [
            {'id': 12, 'name': 'اللون', 'type': 'select', 'is_required': True,
             'values': [{'id': 9, 'name': 'فضي'}]},
            {'id': 13, 'name': 'الاسم', 'type': 'text', 'required': True}
        ]}}

    def test_reads_existing_ids_color_and_text_without_mutation(self):
        before = deepcopy(self.row)
        result = opening_option_source(self.row)
        self.assertEqual([(o['id'], o['name'], o['type']) for o in result['options']],
                         [('12', 'اللون', 'select'), ('13', 'الاسم', 'text')])
        self.assertEqual(result['options'][0]['values'][0]['id'], '9')
        self.assertEqual(self.row, before)

    def test_explicit_canonical_options_win(self):
        for definitions in ([], [{'id':'canonical'}]):
            row = {**self.row, 'options': definitions}
            self.assertIs(opening_option_source(row), row)

    def test_incomplete_definitions_do_not_generate_ids_or_default_required(self):
        for key in ('id','is_required','type'):
            row = deepcopy(self.row)
            del row['raw_salla']['options'][0][key]
            self.assertIsNone(opening_option_source(row)['options'])
        row = deepcopy(self.row)
        del row['raw_salla']['options'][0]['values'][0]['id']
        self.assertIsNone(opening_option_source(row)['options'])


class OpeningCatalogContract(unittest.TestCase):
    def test_cached_options_reach_catalog_and_configuration_only_for_opening(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from operational_purchase_metadata import products, configuration
        row = {'mezan_product_id':'product', 'name':'سلسال', 'sku':'S1',
               'options_count':2, 'raw_salla':{'options':[
                   {'id':12,'name':'اللون','type':'select','is_required':True,'values':[{'id':9,'name':'فضي'}]},
                   {'id':13,'name':'الاسم','type':'text','required':True}]}}
        before = deepcopy(row)
        with patch('operational_purchase_metadata.rows', new=AsyncMock(return_value=[row])):
            normal = asyncio.run(products(None, 'owner'))[0]
            opening = asyncio.run(products(None, 'owner', opening_cached_options=True))[0]
        self.assertEqual(normal['options'], [])
        self.assertTrue(normal['configuration_issues'])
        self.assertEqual(opening['configuration_issues'], [])
        self.assertEqual(row,before)
        for name in ('عبير','فاطمة'):
            config, labels = configuration({'state':'ready','selections':[{'option_id':'12','value_id':'9'}],
                                          'inputs':[{'option_id':'13','value':name}]},opening)
            self.assertEqual(config['inputs'][0]['value'],name)
            self.assertEqual([entry['value'] for entry in labels],['فضي',name])

    def test_ambiguous_cached_identity_still_fails_contract(self):
        from operational_purchase_metadata import option_contract
        row = {'options_count':2,'raw_salla':{'options':[
            {'id':1,'name':'الاسم','required':True,'type':'text'},
            {'id':1,'name':'اللون','required':True,'type':'text'}]}}
        self.assertTrue(option_contract(opening_option_source(row))[1])
