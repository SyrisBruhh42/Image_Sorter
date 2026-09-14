"""Actual CUT-07 controller with simulated API and disposable real Git remotes.

Only API service and native/CI evidence validation are simulated; prepare/action/
check_rules/merge/merge_identity/archive/publish/rename/main_gate/prune/delete/
check_drift/finalize execute actual unchanged source. No network fallback exists.
"""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from scripts import cutover

OLD = 'refs/heads/feature/old-default'
INTEGRATION = 'refs/heads/integration/unified-main'

def git(cwd, *args):
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', *args],
                            cwd=cwd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()

class StatefulBackend(cutover.Backend):
    def __init__(self, checkout, remote, base, candidate, merged, original_auto_delete):
        super().__init__(checkout, 'fixture/repo')
        self.remote = remote
        self.base, self.candidate, self.merged = base, candidate, merged
        self.auto_delete = original_auto_delete
        self.default = OLD[11:]
        self.rules = {}
        self.pr_record = None
        self.calls, self.pushes = [], []
        self.before_push = None
        self.flip_auto_at_merge = False
        self.advance_before_merge_cas = None
        self.qualification_description = None
        self.lose_auto_delete_response = False

    def api(self, path, method='GET', body=None, paginate=False):
        self.calls.append({'path': path, 'method': method, 'body': copy.deepcopy(body)})
        endpoint = self.endpoint()
        if path == endpoint:
            if method == 'PATCH':
                assert set(body) == {'delete_branch_on_merge'}
                self.auto_delete = body['delete_branch_on_merge']
                if self.lose_auto_delete_response:
                    self.lose_auto_delete_response = False
                    raise cutover.GateError('simulated lost setting response')
            else:
                assert method == 'GET'
            return {'id': 123, 'permissions': {'admin': True}, 'default_branch': self.default,
                    'delete_branch_on_merge': self.auto_delete}
        if path.startswith(endpoint + '/rulesets?'):
            assert method == 'GET'
            return [{'id': number} for number in sorted(self.rules)]
        if path == endpoint + '/rulesets':
            assert method == 'POST'
            number = max(self.rules, default=0) + 1
            self.rules[number] = {**copy.deepcopy(body), 'id': number}
            return copy.deepcopy(self.rules[number])
        if path.startswith(endpoint + '/rulesets/'):
            number = int(path.rsplit('/', 1)[1])
            if method == 'DELETE':
                del self.rules[number]
                return {}
            if method == 'PUT':
                self.rules[number] = {**copy.deepcopy(body), 'id': number}
            else:
                assert method == 'GET'
            return copy.deepcopy(self.rules[number])
        if path == endpoint + '/pulls':
            assert method == 'POST' and self.pr_record is None
            assert body['head'] == INTEGRATION[11:] and body['base'] == OLD[11:]
            assert self.remote_refs()[INTEGRATION] == self.candidate
            self.pr_record = {'number': 50, 'html_url': 'https://example.invalid/pull/50',
                              'head': {'sha': self.candidate, 'ref': body['head']},
                              'base': {'ref': body['base']}, 'state': 'open', 'merged': False,
                              'mergeable': True, 'merge_commit_sha': self.merged, 'body': body['body']}
            return copy.deepcopy(self.pr_record)
        if path == endpoint + '/pulls/50/merge':
            assert method == 'PUT' and set(body) == {'sha', 'merge_method'}
            assert body['sha'] == self.candidate and body['merge_method'] == 'merge'
            if self.advance_before_merge_cas:
                git(self.checkout, 'push', 'origin', f'{self.advance_before_merge_cas}:{INTEGRATION}')
                self.pr_record['head']['sha'] = self.advance_before_merge_cas
            if self.remote_refs().get(INTEGRATION) != body['sha']:
                return {'merged': False, 'message': 'Head SHA mismatch'}
            if self.flip_auto_at_merge:
                self.auto_delete = True
            git(self.checkout, 'push', 'origin', f'{self.merged}:{OLD}')
            self.pr_record.update(merged=True, state='closed', merge_commit_sha=self.merged)
            if self.auto_delete:
                git(self.remote, 'update-ref', '-d', INTEGRATION, self.candidate)
            return {'merged': True, 'sha': self.merged}
        if path == endpoint + f'/commits/{self.candidate}/statuses?per_page=100':
            assert method == 'GET'
            return [{'context': cutover.NATIVE_CONTEXT, 'state': 'success', 'creator': {'id': 42},
                     'description': self.qualification_description}]
        if path.startswith(endpoint + '/branches/') and path.endswith('/rename'):
            assert method == 'POST' and body == {'new_name': 'main'}
            assert self.remote_refs().get(OLD) == self.merged
            git(self.remote, 'update-ref', 'refs/heads/main', self.merged)
            git(self.remote, 'update-ref', '-d', OLD, self.merged)
            self.default = 'main'
            self.pr_record['base']['ref'] = 'main'
            return {'name': 'main'}
        raise AssertionError(('Unexpected API route; no live fallback', path, method, body))

    def prs(self):
        if self.pr_record is None:
            return []
        pr = self.pr_record
        return [{'number': 50, 'head_ref': INTEGRATION[11:], 'head_sha': pr['head']['sha'],
                 'head_repository_id': 123, 'base_ref': pr['base']['ref'], 'state': pr['state']}]

    def pr(self, number):
        assert number == 50 and self.pr_record is not None
        return copy.deepcopy(self.pr_record)

    def git(self, *args, **kwargs):
        if 'push' in args:
            self.pushes.append(args)
            if self.before_push:
                callback, self.before_push = self.before_push, None
                callback()
        return super().git(*args, **kwargs)

    def changes(self, suffix=None):
        return [row for row in self.calls if row['method'] != 'GET' and
                (suffix is None or row['path'] == self.endpoint(suffix))]

@pytest.fixture
def scenario(tmp_path, monkeypatch):
    def make(original_auto=True):
        checkout = tmp_path / 'checkout'
        checkout.mkdir()
        remote = tmp_path / 'remote.git'
        git(tmp_path, 'init', '--bare', str(remote))
        git(remote, 'config', 'core.hooksPath', '/dev/null')
        git(checkout, 'init')
        git(checkout, 'config', 'user.name', 'Disposable CUT-07 fixture')
        git(checkout, 'config', 'user.email', 'fixture@example.invalid')
        git(checkout, 'config', 'core.hooksPath', '/dev/null')
        (checkout / 'fixture').write_text('base')
        git(checkout, 'add', 'fixture')
        git(checkout, 'commit', '-m', 'base')
        base = git(checkout, 'rev-parse', 'HEAD')
        (checkout / 'fixture').write_text('qualified final candidate')
        git(checkout, 'commit', '-am', 'qualified candidate')
        candidate = git(checkout, 'rev-parse', 'HEAD')
        tree = git(checkout, 'rev-parse', 'HEAD^{tree}')
        merged = git(checkout, 'commit-tree', tree, '-p', base, '-p', candidate, '-m', 'conditional integration merge')
        advanced = git(checkout, 'commit-tree', tree, '-p', candidate, '-m', 'later same-tree branch identity')
        assert len({base, candidate, merged, advanced}) == 4
        git(checkout, 'remote', 'add', 'origin', str(remote))
        git(checkout, 'push', 'origin', f'{base}:{OLD}')
        git(checkout, 'tag', '-a', 'archive/fixture/base', base, '-m', 'base recovery')
        git(checkout, 'tag', '-a', 'archive/fixture/candidate', candidate, '-m', 'candidate recovery')
        backend = StatefulBackend(checkout, remote, base, candidate, merged, original_auto)
        inventory = {'repository': 'fixture/repo', 'repository_id': 123, 'branches': {OLD: base},
                     'prs': [], 'actor': {'id': 42}, 'rulesets': [], 'delete_branch_on_merge': original_auto}
        plan = {'schema_version': 1, 'run_id': 'cut07', 'inventory': inventory,
                'inventory_sha256': cutover.digest(inventory), 'base_ref': OLD, 'base_sha': base,
                'candidate_sha': candidate, 'candidate_tree': tree, 'integration_ref': INTEGRATION,
                'required_component_ids': sorted(cutover.COMPONENT_IDS), 'archives': backend.local_archives(), 'dispositions': []}
        controller = cutover.Controller(backend, plan, tmp_path / 'journal')
        manifest = tmp_path / 'native-boundary-fixture.json'
        manifest.write_text(json.dumps({'scope': 'Native/CI validation outside this control regression'}))
        native_ref = {'path': str(manifest), 'sha256': cutover.file_digest(manifest)}
        def native_boundary(path, expected_sha, expected_tree, component_ids):
            assert Path(path) == manifest and cutover.file_digest(path) == native_ref['sha256']
            assert expected_sha in (candidate, merged) and expected_tree == tree
            assert component_ids == sorted(cutover.COMPONENT_IDS)
            return {'source_sha': expected_sha, 'source_tree': tree}
        monkeypatch.setattr(cutover, 'verify_qualification', native_boundary)
        monkeypatch.setattr(controller, 'required_ci', lambda oid: {'id': 1, 'head_sha': oid})
        def ci_boundary(check, allowed):
            assert check['head_sha'] == candidate and allowed == {candidate, merged}
            return {'scope': 'CI provenance separately tested'}
        monkeypatch.setattr(controller, 'ci_evidence', ci_boundary)
        def publish_and_qualify():
            controller.prepare()
            controller.archive()
            controller.publish()
            controller.journal.append('qualified', 'result', manifest=native_ref, test_merge_sha=merged)
            backend.qualification_description = f"Receipt sha256 {native_ref['sha256'][:40]}"
        def merge_and_rename():
            publish_and_qualify()
            controller.merge()
            controller.journal.append('merged-qualified', 'result', source_sha=merged, manifest=native_ref)
            controller.rename()
        return {'controller': controller, 'backend': backend, 'candidate': candidate, 'merged': merged,
                'advanced': advanced, 'base': base, 'publish': publish_and_qualify, 'renamed': merge_and_rename}
    return make

@pytest.mark.parametrize('original_auto', [True, False])
def test_prepare_disables_auto_delete_and_idempotent_retry_uses_actual_rules(scenario, original_auto):
    state = scenario(original_auto)
    controller, backend = state['controller'], state['backend']
    controller.prepare()
    assert backend.auto_delete is False and len(backend.changes('')) == int(original_auto)
    assert len(backend.rules) == 3
    assert all(row['body'] == {'delete_branch_on_merge': False} for row in backend.changes(''))
    controller.check_rules()
    mutations = len(backend.changes())
    controller.prepare()
    assert len(backend.changes()) == mutations
    assert controller.journal.last('disable-auto-delete', 'result' if original_auto else 'reconciled')
    backend.auto_delete = True
    with pytest.raises(cutover.GateError, match='Automatic branch deletion'):
        controller.check_rules()

def test_prepare_lost_setting_response_reconciles_without_duplicate_patch(scenario):
    state = scenario(True)
    controller, backend = state['controller'], state['backend']
    backend.lose_auto_delete_response = True
    with pytest.raises(cutover.GateError, match='lost setting response'):
        controller.prepare()
    assert backend.auto_delete is False and len(backend.changes('')) == 1
    controller.prepare()
    assert len(backend.changes('')) == 1
    assert controller.journal.last('disable-auto-delete', 'reconciled')['attribution'] == 'not-inferred'
    controller.check_rules()

def test_merge_uses_exact_candidate_sha_and_disabled_auto_delete_preserves_integration(scenario):
    state = scenario(True)
    controller, backend = state['controller'], state['backend']
    state['publish']()
    controller.merge()
    calls = backend.changes('/pulls/50/merge')
    assert len(calls) == 1 and calls[0]['body'] == {'sha': state['candidate'], 'merge_method': 'merge'}
    assert backend.remote_refs() == {OLD: state['merged'], INTEGRATION: state['candidate']}
    assert controller.journal.last('merge', 'result')['merge_sha'] == state['merged']
    controller.check_drift()

def test_auto_delete_reenabled_before_last_merge_check_prevents_merge_request(scenario):
    state = scenario(True)
    controller, backend = state['controller'], state['backend']
    state['publish']()
    before = backend.remote_refs()
    backend.auto_delete = True
    with pytest.raises(cutover.GateError, match='Automatic branch deletion'):
        controller.merge()
    assert not backend.changes('/pulls/50/merge') and backend.remote_refs() == before
    assert not controller.journal.last('merge', 'intent')

def test_conditional_merge_rejects_head_advance_after_client_preflight(scenario):
    state = scenario(True)
    controller, backend = state['controller'], state['backend']
    state['publish']()
    backend.advance_before_merge_cas = state['advanced']
    with pytest.raises(cutover.GateError, match='successful merge'):
        controller.merge()
    assert backend.remote_refs() == {OLD: state['base'], INTEGRATION: state['advanced']}
    assert not controller.journal.last('merge', 'result')

def test_auto_delete_race_after_final_check_is_detected_without_false_deletion_authorship(scenario):
    state = scenario(True)
    controller, backend = state['controller'], state['backend']
    state['publish']()
    backend.flip_auto_at_merge = True
    controller.merge()
    assert controller.journal.last('merge', 'result')['merge_sha'] == state['merged']
    with pytest.raises(cutover.GateError, match='Remote branches drifted'):
        controller.check_drift()
    with pytest.raises(cutover.GateError, match='Unrecorded branch disappearance'):
        controller.delete_branch(INTEGRATION, state['candidate'])
    assert not controller.journal.last('delete:' + INTEGRATION, 'intent')
    assert not controller.journal.last('delete:' + INTEGRATION, 'result')
    controller.archive_gate(state['candidate'])

@pytest.mark.parametrize('advance', [False, True])
def test_integration_prune_uses_candidate_not_merge_sha_and_preserves_advanced_tip(scenario, advance):
    state = scenario(True)
    controller, backend = state['controller'], state['backend']
    state['renamed']()
    before_pushes = len(backend.pushes)
    if advance:
        backend.before_push = lambda: git(backend.checkout, 'push', 'origin', f"{state['advanced']}:{INTEGRATION}")
        with pytest.raises(cutover.GateError, match='Command failed'):
            controller.prune()
        assert backend.remote_refs()[INTEGRATION] == state['advanced']
        assert not controller.journal.last('delete:' + INTEGRATION, 'result')
    else:
        controller.prune()
        assert backend.remote_refs() == {'refs/heads/main': state['merged']}
        assert controller.journal.last('delete:' + INTEGRATION, 'result')['deleted_sha'] == state['candidate']
    pushes = backend.pushes[before_pushes:]
    assert len(pushes) == 1
    assert f"--force-with-lease={INTEGRATION}:{state['candidate']}" in pushes[0]
    assert f"--force-with-lease={INTEGRATION}:{state['merged']}" not in pushes[0]
    assert ':' + INTEGRATION in pushes[0]
    assert backend.auto_delete is False

@pytest.mark.parametrize('original_auto', [True, False])
def test_finalize_restores_setting_only_after_exact_main_topology_and_retries(scenario, original_auto):
    state = scenario(original_auto)
    controller, backend = state['controller'], state['backend']
    state['renamed']()
    with pytest.raises(cutover.GateError, match='Unexpected branches remain'):
        controller.finalize()
    assert backend.auto_delete is False
    controller.prune()
    backend.pr_record['state'] = 'open'
    with pytest.raises(cutover.GateError, match='Open PRs remain'):
        controller.finalize()
    backend.pr_record['state'] = 'closed'
    controller.journal.append('quality-app-binding', 'result', body=controller.desired_rules()[1])
    controller.finalize()
    assert backend.auto_delete is original_auto
    assert backend.remote_refs() == {'refs/heads/main': state['merged']}
    assert controller.journal.last('complete', 'result')['source_sha'] == state['merged']
    assert not any(row['name'].endswith(' maintenance') for row in backend.rules.values())
    settings_writes = len(backend.changes(''))
    controller.finalize()
    assert len(backend.changes('')) == settings_writes and backend.auto_delete is original_auto
