"""Real Git DAGs: no mocked Git or temporary relaxation of the verifier."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.release_intent_history import INTENT, IntentHistoryError, verify_intent_history
from scripts import emergent_deployment_adapter as adapter


class IntentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'production')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Git fixture')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'commit.gpgsign', 'false')
        self.write(INTENT, 'seed')
        self.commit('source0')
        self.j0 = self.release()
        self.write('prod1.py', 'first')
        self.commit('source1')
        self.j1 = self.release()
        self.write('prod2.py', 'second')
        self.commit('source2')
        self.j2 = self.release()
        self.git('checkout', '-qb', 'candidate', self.j0)
        self.write('candidate.py', 'candidate')
        self.commit('candidate')

    def git(self, *args, ok=True):
        r = subprocess.run(['git', '-C', str(self.repo), *args],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if ok and r.returncode:
            self.fail(r.stderr.decode(errors='replace'))
        return r.stdout.decode().strip()

    def write(self, path, value):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding='utf-8')

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def release(self):
        self.write(INTENT, json.dumps({'kind': adapter.INTENT_KIND, 'protocol_version': 5,
                   'schema_version': adapter.INTENT_SCHEMA_VERSION,
                   'source_git_sha': self.git('rev-parse', 'HEAD')}))
        return self.commit('intent only')

    def merge(self, checkpoint, intent=None):
        self.git('merge', '--no-ff', '--no-commit', checkpoint, ok=False)
        self.write(INTENT, intent if intent is not None else self.git('show', checkpoint + ':' + INTENT))
        return self.commit('merge')

    def proof(self, base, reject=False):
        head = self.git('rev-parse', 'HEAD')
        if reject:
            with self.assertRaises(IntentHistoryError):
                verify_intent_history(self.repo, base, head)
            with patch.object(adapter, 'REPO_ROOT', self.repo):
                with self.assertRaises(adapter.DeploymentAdapterError):
                    adapter._assert_candidate_source_transition(source_git_sha=head, source_base_git_sha=base)
        else:
            verify_intent_history(self.repo, base, head)
            with patch.object(adapter, 'REPO_ROOT', self.repo):
                adapter._assert_candidate_source_transition(source_git_sha=head, source_base_git_sha=base)
                self.assertEqual(adapter.resolve_candidate_source_base(base, head), base)

    def test_linear_candidate(self):
        self.proof(self.j0)

    def test_exact_production_import(self):
        self.merge(self.j2)
        self.proof(self.j2)

    def test_two_approved_imports(self):
        self.merge(self.j1)
        self.merge(self.j2)
        self.proof(self.j2)

    def test_final_changed(self):
        self.write(INTENT, 'tampered')
        self.commit('edit')
        self.proof(self.j0, True)

    def test_modify_revert(self):
        original = self.git('show', 'HEAD:' + INTENT)
        self.write(INTENT, original + ' ')
        self.commit('edit')
        self.write(INTENT, original)
        self.commit('revert')
        self.proof(self.j0, True)

    def test_hidden_side_branch_change(self):
        original = self.git('show', 'HEAD:' + INTENT)
        self.git('checkout', '-qb', 'side')
        self.write(INTENT, 'hidden')
        side = self.commit('side edit')
        self.git('checkout', 'candidate')
        self.write('main.py', 'advance')
        self.commit('advance')
        self.merge(side, original)
        self.proof(self.j0, True)

    def test_untrusted_checkpoint_with_matching_final_blob(self):
        self.git('checkout', '-qb', 'rogue', self.j0)
        self.write(INTENT, 'rogue')
        self.commit('rogue edit')
        self.write(INTENT, self.git('show', self.j0 + ':' + INTENT))
        rogue = self.commit('rogue restore')
        self.git('checkout', 'candidate')
        self.merge(rogue)
        self.proof(self.j0, True)

    def test_merge_resolution_edit_then_restore(self):
        self.merge(self.j2, 'merge edited')
        self.write(INTENT, self.git('show', self.j2 + ':' + INTENT))
        self.commit('restore')
        self.proof(self.j2, True)

    def test_untrusted_import_is_not_laundered_by_later_approved_merge(self):
        self.git('checkout', '-qb', 'untrusted', self.j0)
        self.write(INTENT, self.git('show', self.j2 + ':' + INTENT))
        rogue = self.commit('copied trusted bytes without trusted ancestry')
        self.git('checkout', 'candidate')
        self.merge(rogue)
        self.merge(self.j2)
        self.proof(self.j2, True)

    def test_reversed_parent_import_rejected(self):
        old = self.git('rev-parse', 'HEAD')
        self.merge(self.j2)
        tree = self.git('rev-parse', 'HEAD^{tree}')
        reversed_merge = self.git('commit-tree', tree, '-p', self.j2, '-p', old,
                                  '-m', 'wrong parent order')
        self.git('update-ref', 'refs/heads/candidate', reversed_merge)
        self.proof(self.j2, True)

    def test_delete_restore(self):
        original = self.git('show', 'HEAD:' + INTENT)
        (self.repo / INTENT).unlink()
        self.commit('delete')
        self.write(INTENT, original)
        self.commit('restore')
        self.proof(self.j0, True)

    def test_mode_change_then_restore(self):
        self.git('update-index', '--chmod=+x', INTENT)
        self.git('commit', '-qm', 'mode')
        self.git('update-index', '--chmod=-x', INTENT)
        self.git('commit', '-qm', 'restore mode')
        self.proof(self.j0, True)

    def test_type_change_then_restore(self):
        oid = self.git('rev-parse', 'HEAD:' + INTENT)
        self.git('update-index', '--cacheinfo', '120000,' + oid + ',' + INTENT)
        self.git('commit', '-qm', 'symlink')
        self.git('update-index', '--cacheinfo', '100644,' + oid + ',' + INTENT)
        self.git('commit', '-qm', 'restore')
        self.proof(self.j0, True)

    def test_backward_import_then_latest_restore(self):
        self.merge(self.j2)
        p1 = self.git('rev-parse', 'HEAD')
        # Real commit-tree models an adversarial merge even though ordinary git
        # merge would call the older checkpoint already up-to-date.
        self.write(INTENT, self.git('show', self.j1 + ':' + INTENT))
        self.git('add', INTENT)
        tree = self.git('write-tree')
        bad = self.git('commit-tree', tree, '-p', p1, '-p', self.j1, '-m', 'backward')
        self.git('update-ref', 'refs/heads/candidate', bad)
        self.write(INTENT, self.git('show', self.j2 + ':' + INTENT))
        self.commit('restore latest')
        self.proof(self.j2, True)

    def test_octopus_rejected(self):
        head = self.git('rev-parse', 'HEAD')
        tree = self.git('rev-parse', 'HEAD^{tree}')
        bad = self.git('commit-tree', tree, '-p', head, '-p', self.j0,
                       '-p', self.j1, '-m', 'octopus')
        self.git('update-ref', 'refs/heads/candidate', bad)
        self.proof(self.j0, True)

    def test_shallow_rejected(self):
        clone = Path(self.tmp.name) / 'shallow'
        self.git('clone', '--depth=1', '--branch=candidate', self.repo.as_uri(), str(clone))
        with self.assertRaises(IntentHistoryError):
            verify_intent_history(clone, self.j0, self.git('rev-parse', 'HEAD'))

    def test_missing_object_rejected(self):
        head = self.git('rev-parse', 'HEAD')
        oid = self.git('rev-parse', self.j0 + ':' + INTENT)
        obj = self.repo / '.git/objects' / oid[:2] / oid[2:]
        obj.chmod(0o600)  # Git loose objects are read-only on Windows.
        obj.unlink()
        with self.assertRaises(IntentHistoryError):
            verify_intent_history(self.repo, self.j0, head)

    def test_missing_parent_rejected(self):
        head = self.git('rev-parse', 'HEAD')
        parent = self.git('rev-parse', self.j0 + '^')
        obj = self.repo / '.git/objects' / parent[:2] / parent[2:]
        obj.chmod(0o600)
        obj.unlink()
        with self.assertRaises(IntentHistoryError):
            verify_intent_history(self.repo, self.j0, head)

    def test_replacement_history_rejected(self):
        head = self.git('rev-parse', 'HEAD')
        self.git('replace', head, self.j0)
        with self.assertRaises(IntentHistoryError):
            verify_intent_history(self.repo, self.j0, head)

    def test_cli_and_workflow_gates_share_proof(self):
        self.merge(self.j2)
        import sys
        script = Path(__file__).resolve().parents[2] / 'scripts/release_intent_history.py'
        def cli():
            return subprocess.run([sys.executable, '-B', str(script), '--repo', str(self.repo),
                '--production-base', self.j2, '--candidate', self.git('rev-parse', 'HEAD')],
                capture_output=True).returncode
        self.assertEqual(cli(), 0)
        self.write(INTENT, 'edit')
        self.commit('edit')
        self.assertNotEqual(cli(), 0)
        workflow = (script.parent.parent / '.github/workflows/mezan-production-release.yml').read_text()
        self.assertNotIn('rev-list --full-history', workflow)
        self.assertEqual(workflow.count('python scripts/release_intent_history.py '), 3)

    def test_guard_relation_uses_real_git_history(self):
        from scripts import production_release_guard as guard
        self.merge(self.j2)
        source = self.git('rev-parse', 'HEAD')
        self.write(INTENT, 'new reviewed intent fixture')
        deployment = self.commit('new intent only')
        # Isolate the previous-release schema, not ancestry or Git commands.
        # Its complete schema has a separate existing guard contract suite.
        with patch.object(guard, 'REPO_ROOT', self.repo), patch.object(
                guard, '_assert_previous_release_base', return_value='validated fixture'):
            guard._assert_reviewed_source_relation(source_base_git_sha=self.j2,
                source_git_sha=source, deployment_git_sha=deployment)
        self.git('checkout', '-qb', 'bad-candidate', source)
        original = self.git('show', source + ':' + INTENT)
        self.write(INTENT, 'bad intermediate')
        self.commit('edit')
        self.write(INTENT, original)
        bad_source = self.commit('restore')
        self.write(INTENT, 'new intent')
        bad_deployment = self.commit('intent only')
        with patch.object(guard, 'REPO_ROOT', self.repo), patch.object(
                guard, '_assert_previous_release_base', return_value='validated fixture'):
            with self.assertRaisesRegex(guard.ReleaseGuardError, 'ancestry touches'):
                guard._assert_reviewed_source_relation(source_base_git_sha=self.j2,
                    source_git_sha=bad_source, deployment_git_sha=bad_deployment)


if __name__ == '__main__':
    unittest.main()
