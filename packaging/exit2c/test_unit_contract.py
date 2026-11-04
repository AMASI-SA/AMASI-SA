"""Local controls of the unit acceptance against actual projected synthetic documents."""
import copy
import secrets
import unittest
from acceptance_controller import CheckFailure
from unit_contract import verify_units, CHECKS, unit_lines
from test_unit_projection import synthetic_documents, UnitProjectionTests
from test_resume_diagnostics import controller, shell

class UnitContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=synthetic_documents()

    def run_control(self, mutate, identifier):
        data=copy.deepcopy(self.original)
        mutate(data)
        marker=secrets.token_hex(24)
        refs=[]
        def action(state):
            refs.append(state);state['owner_cookie']=marker
            verify_units(**data,state=state)
        result,reply=controller('prep-resume',action)
        parsed=shell('prep-resume',reply)
        self.assertTrue(result==parsed.returncode==1,'CONTROL_NOT_REJECTED')
        self.assertTrue(parsed.stdout.endswith('FAIL prep-resume ASSERTION_FAILED '+identifier+'\n'),'WRONG_CHECK')
        self.assertTrue(marker not in parsed.stdout+parsed.stderr+reply,'SENSITIVE_OUTPUT')
        self.assertTrue(all(not s for s in refs),'STATE_NOT_CLEARED')
        return parsed.stdout

    def test_healthy_actual_projection_passes_all_predicates(self):
        data=copy.deepcopy(self.original)
        state={}
        verify_units(**data,state=state)
        lines=unit_lines(state['_unit_evidence'])
        self.assertTrue('UNITS ALLOCATIONS ACTUAL 8' in lines and 'UNITS PIECES ACTUAL 8' in lines)
        self.assertTrue(all(not line.endswith(('MISSING 1','DUPLICATE 1','UNEXPECTED 1','MISMATCH 1')) for line in lines))
        def action(s):verify_units(**data,state=s)
        result,reply=controller('prep-resume',action)
        self.assertTrue(result==0)
        for identifier in CHECKS:
            self.assertTrue('CHECK prep-resume '+identifier+' PASS\n' in reply,'PREDICATE_NOT_COMPLETED')

    def test_missing_full_specs_rejected_even_with_pdf_and_other_text(self):
        def change(d):
            p=d['pieces'][0];p['specifications_snapshot']=[]
            p['pdf_text']=' '.join(d['expected'][(p['order_number'],p['order_item_id'])]['options'].values())
        self.run_control(change,'UNIT_SPECIFICATIONS')

    def test_missing_or_changed_each_spec_is_rejected(self):
        for index in (0,1):
            for missing in (False,True):
                def change(d):
                    fields=d['pieces'][0]['specifications_snapshot']
                    if missing: fields.pop(index)
                    else: fields[index]['value']='changed'
                self.run_control(change,'UNIT_SPECIFICATIONS')

    def test_duplicate_spec_or_moved_specs_are_rejected(self):
        self.run_control(lambda d:d['pieces'][0]['specifications_snapshot'].append(copy.deepcopy(d['pieces'][0]['specifications_snapshot'][0])),'UNIT_SPECIFICATIONS')
        def move(d):
            d['pieces'][0]['specifications_snapshot']=copy.deepcopy(d['pieces'][4]['specifications_snapshot'])
        self.run_control(move,'UNIT_SPECIFICATIONS')

    def test_extra_options_missing_changed_or_moved_are_rejected(self):
        for mode in ('missing','changed','moved','extra'):
            def change(d):
                d['pieces'][0]['product_options_snapshot']=(
                    {} if mode=='missing' else
                    copy.deepcopy(d['pieces'][4]['product_options_snapshot']) if mode=='moved' else
                    {'النقش':'changed'} if mode=='changed' else {'النقش':'نور','اللون':'ذهبي'})
            self.run_control(change,'UNIT_PROJECTED_OPTIONS')

    def test_line_color_and_spec_disagreement_rejected(self):
        self.run_control(lambda d:d['batches'][0]['lines'][0].update(color='changed'),'UNIT_SPECIFICATIONS')
        self.run_control(lambda d:d['batches'][0]['lines'][0]['file_spec_fields'].pop(),'UNIT_SPECIFICATIONS')

    def test_allocation_and_piece_identity_controls_report_exact_counts(self):
        for kind,identifier in (('allocations','UNIT_ALLOCATION_IDENTITIES'),('pieces','UNIT_PIECE_IDENTITIES')):
            for mode,metric in (('missing','MISSING'),('duplicate','DUPLICATE'),('extra','UNEXPECTED')):
                def change(d):
                    rows=d[kind]
                    if mode=='missing':rows.pop()
                    elif mode=='duplicate':rows.append(copy.deepcopy(rows[0]))
                    else:
                        extra=copy.deepcopy(rows[0]);extra['unit_index']=99;rows.append(extra)
                output=self.run_control(change,identifier)
                self.assertTrue('UNITS '+kind.upper()+' '+metric+' 1\n' in output,'COUNT_NOT_MEASURED')

    def test_employee_and_committed_state_controls(self):
        self.run_control(lambda d:d['pieces'][0].update(responsible_employee_id='wrong'),'UNIT_EMPLOYEE_ASSIGNMENT')
        self.run_control(lambda d:d['registries'][0].update(responsible_employee_id='wrong'),'UNIT_EMPLOYEE_ASSIGNMENT')
        self.run_control(lambda d:d['allocations'][0].update(status='reserved'),'UNIT_ALLOCATION_STATUS')

    def test_durable_piece_identity_missing_duplicate_or_mismatched(self):
        self.run_control(lambda d:d['pieces'][0].pop('id'),'UNIT_PIECE_IDENTITIES')
        self.run_control(lambda d:d['pieces'][0].update(piece_id='wrong'),'UNIT_PIECE_IDENTITIES')
        def duplicate(d):
            d['pieces'][1].update(id=d['pieces'][0]['id'],piece_id=d['pieces'][0]['piece_id'])
        output=self.run_control(duplicate,'UNIT_PIECE_IDENTITIES')
        self.assertTrue('UNITS PIECES DUPLICATE 1\n' in output,'COUNT_NOT_MEASURED')

    def test_file_batch_and_line_quantity_controls(self):
        for field in ('file_number','batch_id','product_id','group_key','user_id'):
            self.run_control(lambda d:d['pieces'][0].update({field:'wrong'}),'UNIT_BATCH_LINK')
        self.run_control(lambda d:d['batches'][0]['lines'][0].update(quantity=3),'UNIT_BATCH_LINK')
        self.run_control(lambda d:d['files'][0].update(file_number='wrong'),'UNIT_BATCH_LINK')

    def test_unknown_unit_protocol_and_wrong_phase_cannot_escape(self):
        marker=secrets.token_hex(24)
        self.assertTrue(unit_lines([('PIECES',marker,1)])==['UNITS unavailable'])
        for line in ('UNITS '+marker+' ACTUAL 1','UNITS PIECES ACTUAL 10001','UNITS PIECES '+marker+' 1'):
            parsed=shell('prep-resume',line+'\nPASS prep-resume\n')
            self.assertTrue(parsed.returncode==1 and marker not in parsed.stdout+parsed.stderr)
        parsed=shell('prep-finish','UNITS PIECES ACTUAL 8\nPASS prep-finish\n')
        self.assertTrue(parsed.returncode==1)

if __name__=='__main__':
    unittest.main()
