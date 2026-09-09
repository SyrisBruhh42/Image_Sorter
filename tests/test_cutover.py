"""Cutover controller failure injection; all Git remotes are disposable local repos."""

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.cutover import (
    CI_JOBS,
    COMPONENT_IDS,
    Backend,
    Controller,
    GateError,
    Journal,
    canonical,
    contains,
    digest,
    file_digest,
    immutable_json,
    make_plan,
    ref,
    verify_qualification,
)

BASE = "1" * 40
CANDIDATE = "2" * 40
TREE = "3" * 40
OLD = "refs/heads/feature/old-default"
INTEGRATION = "refs/heads/integration/unified-main"


def minimal_plan():
    inventory = {
        "repository": "owner/repo", "repository_id": 123,
        "branches": {OLD: BASE, "refs/heads/topic": BASE}, "prs": [],
        "actor": {"id": 42, "login": "operator"}, "rulesets": [],
    }
    return {
        "schema_version": 1, "run_id": "test", "inventory": inventory,
        "inventory_sha256": digest(inventory), "base_ref": OLD, "base_sha": BASE,
        "candidate_sha": CANDIDATE, "candidate_tree": TREE, "integration_ref": INTEGRATION,
        "required_component_ids": sorted(COMPONENT_IDS), "archives": [], "dispositions": [],
    }


class StubBackend:
    repository = "owner/repo"

    def endpoint(self, suffix=""):
        return f"repos/owner/repo{suffix}"


def test_immutable_inventory_cannot_be_replaced(tmp_path):
    path = tmp_path / "inventory.json"
    immutable_json(path, {"sha": BASE})
    immutable_json(path, {"sha": BASE})
    with pytest.raises(GateError, match="overwrite"):
        immutable_json(path, {"sha": CANDIDATE})
    assert json.loads(path.read_text()) == {"sha": BASE}


def test_unknown_remote_success_reconciles_without_repeating_mutation(tmp_path):
    state = {"value": None, "calls": 0}
    controller = Controller(StubBackend(), minimal_plan(), tmp_path)

    def uncertain_write():
        state["calls"] += 1
        state["value"] = CANDIDATE
        raise GateError("network response lost")

    with pytest.raises(GateError, match="response lost"):
        controller.action("publish", None, CANDIDATE, uncertain_write, lambda: state["value"])
    resumed = Controller(StubBackend(), minimal_plan(), tmp_path)
    resumed.action("publish", None, CANDIDATE, uncertain_write, lambda: state["value"])
    assert state["calls"] == 1
    event = resumed.journal.last("publish", "reconciled")
    assert event["attribution"] == "not-inferred"
    assert event["observed"] == CANDIDATE


def test_changed_remote_state_cannot_be_retried_as_expected_state(tmp_path):
    controller = Controller(StubBackend(), minimal_plan(), tmp_path)
    calls = []
    with pytest.raises(GateError, match="Unexpected state"):
        controller.action("delete", BASE, None, lambda: calls.append(True), lambda: CANDIDATE)
    assert not calls


def test_torn_completion_preserves_intent_and_damaged_bytes(tmp_path):
    journal = Journal(tmp_path, minimal_plan())
    journal.intent("delete:refs/heads/topic", expected=BASE, desired=None)
    with journal.path.open("ab") as stream:
        stream.write(b'{"incomplete":')
    resumed = Journal(tmp_path, minimal_plan())
    assert len(resumed.events) == 1
    assert resumed.events[0]["stage"] == "intent"
    assert next(tmp_path.glob("events.torn-*")).read_bytes() == b'{"incomplete":'
    resumed.append("delete:refs/heads/topic", "reconciled", observed=None)
    assert len(Journal(tmp_path, minimal_plan()).events) == 2


def test_modified_complete_journal_record_fails_closed(tmp_path):
    journal = Journal(tmp_path, minimal_plan())
    journal.intent("publish", expected=None, desired=CANDIDATE)
    row = json.loads(journal.path.read_text())
    row["desired"] = BASE
    journal.path.write_bytes(canonical(row) + b"\n")
    with pytest.raises(GateError, match="hash mismatch"):
        Journal(tmp_path, minimal_plan())


def test_journal_cannot_resume_under_a_new_candidate_plan(tmp_path):
    journal = Journal(tmp_path, minimal_plan())
    journal.intent("publish", expected=None, desired=CANDIDATE)
    altered = minimal_plan()
    altered["candidate_sha"] = "4" * 40
    with pytest.raises(GateError, match="different plan"):
        Journal(tmp_path, altered)


@pytest.mark.parametrize("value", ["main", "refs/heads/a..b", "refs/heads/-bad\n", "refs/heads/a*", "refs/heads/a.lock", "refs/heads/.hidden"])
def test_invalid_or_unqualified_refs_are_rejected(value):
    with pytest.raises(GateError):
        ref(value)


def test_valid_full_ref_with_slashes_is_supported():
    assert ref("refs/heads/task/p01-mutation-engine") == "refs/heads/task/p01-mutation-engine"


def evidence_file(root, name, content):
    path = root / name
    path.write_bytes(content)
    return {"path": str(path), "sha256": file_digest(path)}


def qualified_manifest(root):
    from scripts.qualification_schema import (
        CASE_CRITERIA,
        COMPONENT_CRITERIA,
        FORMAT_COVERAGE,
    )

    shared = evidence_file(root, "native-evidence.json", b'{"observed": true}')
    identity = {"schema_version": 1, "source_sha": CANDIDATE, "source_tree": TREE,
                "dirty": False, "source_files": {"source.py": "a" * 64}}

    def json_file(name, value):
        return evidence_file(root, name, json.dumps(value).encode())

    components = [{"id": name, "manifest": json_file(f"component-{name}.json", {
        "id": name, "version": "1", "sha256": "a" * 64, "files": {"data": {"sha256": "b" * 64}}})}
        for name in sorted(COMPONENT_IDS)]
    component_refs = {item["id"]: item["manifest"] for item in components}
    fixtures = [evidence_file(root, "sample.jpg", b"disposable image"),
                evidence_file(root, "sample.jpg.txt", b"disposable sidecar")]
    artifacts = []
    for index, kind in enumerate(("wheel", "onedir", "appimage"), 1):
        runtime = root / f"runtime-{kind}"
        runtime.mkdir()
        catalog_dir = runtime / "imagesorter/resources"
        catalog_dir.mkdir(parents=True)
        catalog_file = evidence_file(catalog_dir, "component_catalog.json", json.dumps({
            "schema_version": 1, "components": [json.loads(Path(item["manifest"]["path"]).read_text()) for item in components]}).encode())
        artifact = {**evidence_file(runtime, "launcher", kind.encode()), "kind": kind}
        artifact["build"] = json_file(f"build-{kind}.json", {
            **identity, "launch": {k: artifact[k] for k in ("path", "sha256")}, "delivery": shared,
            "runtime_root": str(runtime), "runtime_files": [
                {"relative_path": "launcher", "sha256": artifact["sha256"]},
                {"relative_path": "imagesorter/resources/component_catalog.json", "sha256": catalog_file["sha256"]}],
            "build_log": shared})
        trace = evidence_file(root, f"trace-{kind}.{index}",
                              f'100.0 execve("{artifact["path"]}", [], []) = 0\n131.0 +++ exited with 0 +++\n'.encode())
        telemetry = json_file(f"telemetry-{kind}.json", {"process_ids": [index], "network_attempts": [], "model_open_attempts": []})
        diagnostic = json_file(f"diagnostic-{kind}.json", {
            "schema_version": 1, "pid": index, "profile_root": str(root / f"profile-{kind}"),
            "build_identity": identity, "events": [
                {"event": "image_presented", "backend": "xcb", "generation": 1, "width": 160, "height": 100,
                 "filepath": fixtures[0]["path"], "module_origin": str(runtime / "imagesorter/__init__.py"), "monotonic": 10},
                {"event": "gui_shutdown", "elapsed_ms": 100, "monotonic": 41}]})
        artifact["smoke"] = json_file(f"smoke-{kind}.json", {
            **identity, "artifact": {k: artifact[k] for k in ("path", "sha256", "kind")},
            "complete": False, "native": True, "display_backend": "xcb", "returncode": 0,
            "all_processes_stopped": True, "error": None, "first_launch_observation_seconds": 30,
            "observation_started_unix": 100, "observation_ended_unix": 131, "run_id": f"test-{kind}",
            "effective_profile_root": str(root / f"profile-{kind}"), "diagnostic": diagnostic,
            "telemetry": telemetry, "traces": [trace],
            "fixtures_before": json_file(f"fixtures-before-{kind}.json", fixtures),
            "fixtures_after": json_file(f"fixtures-after-{kind}.json", fixtures),
            "base_first_launch_network_attempts": 0, "base_model_open_attempts": 0, "no_model_files": True,
            "ready_identity_verified": True, "fixture_hashes_unchanged": True, "build_identity_verified": True})
        artifacts.append(artifact)
    ids = {f"KDE-{i:02d}" for i in range(1, 15)} | {f"COMPONENT:{name}" for name in COMPONENT_IDS}
    case_records = []
    mutation = json_file("mutation.json", {"schema_version": 1, "operation_id": "test", "state": "completed"})
    gpu = json_file("gpu.json", {"provider": "CUDAExecutionProvider", "cuda_compute_events": 1,
                                "session_providers": ["CUDAExecutionProvider"], "node_providers": [{"node": "test", "provider": "CUDAExecutionProvider"}],
                                "tensor_sha256": "a" * 64})
    for artifact in artifacts:
        for index, name in enumerate(sorted(ids)):
            observation = {**identity, "kind": "operator-observation", "id": name,
                "artifact_sha256": artifact["sha256"], "operator": "UNIT TEST FIXTURE, NOT NATIVE EVIDENCE",
                "run_id": f"test-{artifact['kind']}", "observed_at": "2026-09-08T00:00:00+00:00",
                "expected": "fixture", "observed": "fixture", "steps": ["Synthetic verifier unit test only"],
                "supporting_evidence": [shared],
                "criteria": dict.fromkeys(CASE_CRITERIA.get(name, COMPONENT_CRITERIA), True),
                "fixtures_before": [{"relative_path": "fixture.jpg", "sha256": "a" * 64, "size": 1}],
                "fixtures_after": [{"relative_path": "fixture.jpg", "sha256": "a" * 64, "size": 1}],
                "mutation_receipts": [mutation], "gui_shutdown_ms": 100, "readonly_reap_ms": 100,
                "mutation_drain_completed": True, "all_processes_stopped": True}
            if name.startswith("COMPONENT:"):
                component_id = name.split(":", 1)[1]
                observation["component_manifest_sha256"] = component_refs[component_id]["sha256"]
                observation["formats"] = {fmt: {"status": "passed", "fixture": fixtures[0]} for fmt in FORMAT_COVERAGE.get(component_id, ())}
                observation.update(gpu_result=gpu, cpu_gpu_max_abs_error=0.0, cpu_gpu_tolerance=.001,
                                   forced_fallback_reason="fixture GPU unavailable")
            case_records.append({"id": name, "artifact_kind": artifact["kind"], "status": "passed", "evidence": [shared],
                                 "observation": json_file(f"observation-{artifact['kind']}-{index}.json", observation)})
    receipt = {
        "schema_version": 1, "complete": True, "source_sha": CANDIDATE, "source_tree": TREE,
        "profile_id": "linux-x86_64-full", "display_backend": "xcb", "session_type": "x11",
        "desktop": "KDE", "native": True, "artifacts": artifacts,
        "components": components,
        "cases": case_records,
        "all_processes_stopped": True, "base_first_launch_network_attempts": 0,
    }
    path = root / "qualification.json"
    path.write_text(json.dumps(receipt))
    return path, receipt


def test_complete_exact_native_manifest_is_accepted(tmp_path):
    path, expected = qualified_manifest(tmp_path)
    assert verify_qualification(path, CANDIDATE, TREE, COMPONENT_IDS) == expected


@pytest.mark.parametrize("alteration", ["old_sha", "old_tree", "headless", "skip", "missing_component", "active_worker", "missing_egress", "missing_case", "duplicate_case", "altered_evidence"])
def test_native_false_evidence_is_rejected(tmp_path, alteration):
    path, receipt = qualified_manifest(tmp_path)
    if alteration == "old_sha":
        receipt["source_sha"] = BASE
    elif alteration == "old_tree":
        receipt["source_tree"] = BASE
    elif alteration == "headless":
        receipt["display_backend"] = "offscreen"
    elif alteration == "skip":
        receipt["cases"][0]["status"] = "skipped"
    elif alteration == "missing_component":
        receipt["components"].pop()
    elif alteration == "active_worker":
        receipt["all_processes_stopped"] = False
    elif alteration == "missing_egress":
        receipt.pop("base_first_launch_network_attempts")
    elif alteration == "missing_case":
        receipt["cases"].pop()
    elif alteration == "duplicate_case":
        receipt["cases"].append(receipt["cases"][0])
    else:
        (tmp_path / "native-evidence.json").write_text("changed after qualification")
    path.write_text(json.dumps(receipt))
    with pytest.raises(GateError):
        verify_qualification(path, CANDIDATE, TREE, COMPONENT_IDS)


def test_policy_comparison_does_not_accept_extra_bypass_actor():
    expected = {"enforcement": "active", "bypass_actors": [], "rules": [{"type": "deletion"}]}
    actual = {**expected, "id": 5}
    assert contains(actual, expected)
    assert not contains({**actual, "bypass_actors": [{"actor_type": "User", "actor_id": 42}]}, expected)
    assert not contains({**actual, "enforcement": "evaluate"}, expected)


def git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class LocalRemote(Backend):
    def __init__(self, checkout, heads, before_push=None, after_push=None):
        super().__init__(checkout, "owner/repo")
        self.heads = heads
        self.before_push = before_push
        self.after_push = after_push
        self.pull_requests = []
        self.pushes = []

    def api(self, path, method="GET", body=None, paginate=False):
        assert method == "GET"
        return {"default_branch": "main"}

    def prs(self):
        return list(self.pull_requests)

    def git(self, *args, **kwargs):
        if "push" in args:
            self.pushes.append(args)
            if self.before_push:
                callback, self.before_push = self.before_push, None
                callback()
            result = super().git(*args, **kwargs)
            if self.after_push:
                callback, self.after_push = self.after_push, None
                callback()
            return result
        return super().git(*args, **kwargs)


@pytest.fixture
def remote_fixture(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(checkout, "init")
    git(checkout, "config", "user.name", "Cutover test")
    git(checkout, "config", "user.email", "cutover-test@example.invalid")
    (checkout / "file").write_text("first")
    git(checkout, "add", "file")
    git(checkout, "commit", "-m", "first")
    first = git(checkout, "rev-parse", "HEAD")
    (checkout / "file").write_text("second")
    git(checkout, "commit", "-am", "second")
    second = git(checkout, "rev-parse", "HEAD")
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "push", "origin", f"{first}:refs/heads/topic")
    git(checkout, "push", "origin", f"{second}:refs/tags/object-preservation")
    plan = minimal_plan()
    plan["inventory"]["branches"]["refs/heads/topic"] = first
    plan["inventory_sha256"] = digest(plan["inventory"])
    backend = LocalRemote(checkout, remote)
    controller = Controller(backend, plan, tmp_path / "evidence")
    controller.archive_gate = lambda oid=None: None
    return checkout, remote, first, second, backend, controller


def test_real_git_lease_rejects_update_between_preflight_and_push(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    backend.before_push = lambda: git(remote, "update-ref", "refs/heads/topic", second, first)
    with pytest.raises(GateError, match="Command failed"):
        controller.delete_branch("refs/heads/topic", first)
    assert git(remote, "rev-parse", "refs/heads/topic") == second
    assert f"--force-with-lease=refs/heads/topic:{first}" in backend.pushes[0]
    assert ":refs/heads/topic" in backend.pushes[0]
    assert not controller.journal.last("delete:refs/heads/topic", "result")


def test_real_git_deletion_removes_only_exact_target(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    git(remote, "update-ref", "refs/heads/topic-preserved", second)
    controller.delete_branch("refs/heads/topic", first)
    assert "refs/heads/topic" not in backend.remote_refs()
    assert git(remote, "rev-parse", "refs/heads/topic-preserved") == second
    assert controller.journal.last("delete:refs/heads/topic", "result")["deleted_sha"] == first


def test_unplanned_branch_and_default_cannot_be_deleted(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    for name in ("refs/heads/main", "refs/heads/other", OLD):
        with pytest.raises(GateError):
            controller.delete_branch(name, first)
    assert not backend.pushes


def test_pr_dependency_race_restores_archived_branch_without_overwrite(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    backend.after_push = lambda: backend.pull_requests.append({
        "number": 99, "state": "open", "base_ref": "topic", "head_ref": "new-feature",
        "head_repository_id": 999,
    })
    with pytest.raises(GateError, match="restored"):
        controller.delete_branch("refs/heads/topic", first)
    assert git(remote, "rev-parse", "refs/heads/topic") == first
    assert "--force-with-lease=refs/heads/topic:" in backend.pushes[1]
    assert controller.journal.last("restore:refs/heads/topic", "result")


def test_recreated_ref_after_delete_is_preserved(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    backend.after_push = lambda: git(remote, "update-ref", "refs/heads/topic", second)
    with pytest.raises(GateError, match="recreated"):
        controller.delete_branch("refs/heads/topic", first)
    assert git(remote, "rev-parse", "refs/heads/topic") == second
    assert len(backend.pushes) == 1


def test_absence_after_lost_delete_response_is_not_falsely_attributed(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    controller.journal.intent("delete:refs/heads/topic", expected=first, desired=None)
    git(remote, "update-ref", "-d", "refs/heads/topic", first)
    controller.delete_branch("refs/heads/topic", first)
    assert controller.journal.last("delete:refs/heads/topic", "reconciled")["attribution"] == "not-inferred"
    assert not backend.pushes


def test_invalid_plan_stops_before_any_remote_action(tmp_path):
    plan = minimal_plan()
    plan["inventory"]["branches"]["refs/heads/new"] = BASE
    with pytest.raises(GateError, match="Inventory was modified"):
        Controller(StubBackend(), plan, tmp_path)


def test_ci_aggregate_rejects_missing_or_skipped_jobs(tmp_path):
    # Exercise the actual embedded aggregate program with its runtime environment.
    ci = Path(__file__).parents[1] / ".github/workflows/ci.yml"
    content = ci.read_text()
    code = content.split("python - <<'PY'", 1)[1].split("\n          PY", 1)[0]
    code = "\n".join(line.removeprefix("          ") for line in code.splitlines())
    jobs = ("quality", "x11-smoke", "native-portability", "linux-artifacts", "optional-ai-integration")
    import os
    import sys

    git(tmp_path, "init")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture")
    env = {**os.environ, "CI_CHECK_HEAD_SHA": CANDIDATE, "CI_REPOSITORY_ID": "123", "CI_WORKFLOW_RUN_ID": "1", "CI_RUN_ATTEMPT": "1"}

    for outcome, expected in (("success", 0), ("skipped", 1), ("failure", 1),
                              ("cancelled", 1), ("neutral", 1), ("missing", 1)):
        results = {name: {"result": "success"} for name in jobs}
        if outcome == "missing":
            del results["quality"]
        else:
            results["quality"]["result"] = outcome
        result = subprocess.run([sys.executable, "-c", code], env={**env, "JOB_RESULTS": json.dumps(results)}, capture_output=True, cwd=tmp_path)
        assert result.returncode == expected, result.stderr


def test_plan_requires_archive_for_every_branch(tmp_path):
    class PlanningBackend(StubBackend):
        def ancestor(self, older, newer):
            return True

        def git(self, *args):
            return "Test <test@example.invalid> 1 +0000"

        def tree(self, oid):
            return TREE

    inventory = {**minimal_plan()["inventory"], "schema_version": 1, "candidate_sha": CANDIDATE,
                 "default_ref": OLD, "can_administer": True, "allow_merge_commit": True,
                 "default_protected": False, "archives": []}
    bundle = tmp_path / "bundle"
    bundle.write_bytes(b"placeholder")
    with pytest.raises(GateError, match="lacks an archive"):
        make_plan(PlanningBackend(), inventory, bundle, [], "test")


def test_digest_uses_canonical_field_order():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest({"a": 1}) == hashlib.sha256(canonical({"a": 1})).hexdigest()


@pytest.mark.parametrize("conclusion,slug,count", [("skipped", "github-actions", 1), ("neutral", "github-actions", 1), ("success", "other-app", 1), ("success", "github-actions", 0), ("success", "github-actions", 2)])
def test_ci_requires_unique_actual_success_from_actions(tmp_path, conclusion, slug, count):
    class ChecksBackend(StubBackend):
        def api(self, path, **kwargs):
            return [{"name": "Image Sorter required CI", "head_sha": CANDIDATE,
                    "status": "completed", "conclusion": conclusion, "app": {"slug": slug}}] * count

    controller = Controller(ChecksBackend(), minimal_plan(), tmp_path)
    with pytest.raises(GateError):
        controller.required_ci(CANDIDATE)


@pytest.mark.parametrize("changed", ["tree", "candidate_parent", "base_parent", "squash"])
def test_merged_identity_rejects_unqualified_result(tmp_path, changed):
    class MergeBackend(StubBackend):
        def git(self, *args):
            if args[0] == "show":
                return {
                    "tree": f"{BASE} {CANDIDATE}", "candidate_parent": f"{BASE} {'4' * 40}",
                    "base_parent": f"{'4' * 40} {CANDIDATE}", "squash": BASE,
                }[changed]
            return ""

        def ancestor(self, older, newer):
            return True

        def tree(self, oid):
            return BASE if changed == "tree" else TREE

    controller = Controller(MergeBackend(), minimal_plan(), tmp_path)
    controller.find_integration_pr = lambda: {"merged": True, "head": {"sha": CANDIDATE}, "merge_commit_sha": "5" * 40}
    with pytest.raises(GateError):
        controller.merge_identity()


def test_archive_is_restored_from_bundle_and_remote_before_prune(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    git(checkout, "tag", "-a", "archive/test/base", first, "-m", "base archive")
    git(checkout, "tag", "-a", "archive/test/candidate", second, "-m", "candidate archive")
    controller.plan["archives"] = backend.local_archives()
    controller.archive()
    local = controller.journal.last("archive-local", "result")
    published = controller.journal.last("archive-publish", "result")
    assert Path(local["restored"]).is_dir()
    assert Path(published["restored"]).is_dir()
    assert Path(local["bundle"]["path"]).is_file()
    for entry in controller.plan["archives"]:
        assert git(remote, "rev-parse", entry["ref"]) == entry["tag_sha"]
        assert git(remote, "rev-parse", f"{entry['ref']}^{{commit}}") == entry["commit_sha"]


def test_changed_annotated_tag_is_not_accepted_even_when_commit_matches(remote_fixture):
    checkout, remote, first, second, backend, controller = remote_fixture
    name = "archive/test/base"
    git(checkout, "tag", "-a", name, first, "-m", "original annotation")
    git(checkout, "push", "origin", f"refs/tags/{name}:refs/tags/{name}")
    original = git(remote, "rev-parse", f"refs/tags/{name}")
    git(checkout, "tag", "-d", name)
    git(checkout, "tag", "-a", name, first, "-m", "different annotation")
    controller.plan["archives"] = backend.local_archives()
    with pytest.raises(GateError, match="archive tag differs"):
        controller.archive()
    assert git(remote, "rev-parse", f"refs/tags/{name}") == original
    assert not controller.journal.last("archive-publish", "result")


def ci_provenance_backend():
    class EvidenceBackend(StubBackend):
        def __init__(self):
            self.runs = [{"id": 10, "run_attempt": 2, "check_suite_id": 30,
                          "head_sha": CANDIDATE, "status": "completed", "conclusion": "success",
                          "path": ".github/workflows/ci.yml"}]
            self.artifacts = [{"id": 40, "name": "cutover-ci-10-2", "expired": False}]
            self.evidence = {"schema_version": 1, "source_sha": CANDIDATE, "source_tree": TREE,
                             "check_head_sha": CANDIDATE, "repository_id": "123",
                             "workflow_run_id": "10", "run_attempt": "2",
                             "jobs": {name: {"result": "success"} for name in CI_JOBS}}
            self.filename = "cutover-ci.json"

        def api(self, path, **kwargs):
            if "/actions/runs?" in path:
                return self.runs
            if "/actions/runs/10/artifacts?" in path:
                return self.artifacts
            pytest.fail(f"Unexpected API read: {path}")

        def command(self, args, **kwargs):
            assert kwargs["binary"] and args[-1] == "repos/owner/repo/actions/artifacts/40/zip"
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr(self.filename, json.dumps(self.evidence))
            return output.getvalue()

    return EvidenceBackend()


@pytest.mark.parametrize("source", [CANDIDATE, "4" * 40])
def test_ci_provenance_binds_check_head_to_actual_qualified_checkout(tmp_path, source):
    backend = ci_provenance_backend()
    backend.evidence["source_sha"] = source
    controller = Controller(backend, minimal_plan(), tmp_path)
    result = controller.ci_evidence({"head_sha": CANDIDATE, "check_suite": {"id": 30}}, {CANDIDATE, source})
    assert result["checkout_sha"] == source
    assert file_digest(result["path"]) == result["sha256"]
    assert json.loads(Path(result["path"]).read_text())["jobs"]["quality"]["result"] == "success"


@pytest.mark.parametrize("drift", ["source_sha", "source_tree", "check_head_sha", "repository_id",
                                   "workflow_run_id", "run_attempt", "skipped_job", "missing_job",
                                   "workflow", "expired", "ambiguous_artifact", "zip_path"])
def test_ci_provenance_rejects_forged_stale_missing_or_skipped_evidence(tmp_path, drift):
    backend = ci_provenance_backend()
    if drift in {"source_sha", "source_tree", "check_head_sha"}:
        backend.evidence[drift] = "9" * 40
    elif drift in {"repository_id", "workflow_run_id", "run_attempt"}:
        backend.evidence[drift] = 999
    elif drift == "skipped_job":
        backend.evidence["jobs"]["quality"]["result"] = "skipped"
    elif drift == "missing_job":
        del backend.evidence["jobs"]["quality"]
    elif drift == "workflow":
        backend.runs[0]["path"] = ".github/workflows/pretend.yml"
    elif drift == "expired":
        backend.artifacts[0]["expired"] = True
    elif drift == "ambiguous_artifact":
        backend.artifacts *= 2
    elif drift == "zip_path":
        backend.filename = "../cutover-ci.json"
    controller = Controller(backend, minimal_plan(), tmp_path)
    with pytest.raises(GateError):
        controller.ci_evidence({"head_sha": CANDIDATE, "check_suite": {"id": 30}}, {CANDIDATE})
    assert not (tmp_path / "ci-10-2.json").exists()


def test_post_merge_ci_cannot_reuse_candidate_checkout_receipt(tmp_path):
    backend = ci_provenance_backend()
    merged = "5" * 40
    backend.runs[0]["head_sha"] = merged
    backend.evidence["check_head_sha"] = merged
    controller = Controller(backend, minimal_plan(), tmp_path)
    with pytest.raises(GateError, match="checkout identity"):
        controller.ci_evidence({"head_sha": merged, "check_suite": {"id": 30}}, {merged})


def rewrite_reference(entry, mutate):
    path = Path(entry["path"])
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value))
    entry["sha256"] = file_digest(path)


@pytest.mark.parametrize("contradiction", ["build_source", "dirty_build", "runtime_extra", "runtime_missing",
                                          "headless_smoke", "short_window", "wrong_origin", "embedded_source",
                                          "raw_egress", "missing_process_exit", "wrong_exec", "anonymous_case",
                                          "untested_criterion", "missing_mutation", "undrained_mutator", "component_swap",
                                          "missing_raw_format", "gpu_enumeration_only"])
def test_semantic_native_gate_rejects_hash_consistent_false_claims(tmp_path, contradiction):
    path, receipt = qualified_manifest(tmp_path)
    artifact = receipt["artifacts"][0]
    if contradiction in {"build_source", "dirty_build", "runtime_extra", "runtime_missing"}:
        def change_build(value):
            if contradiction == "build_source":
                value["source_sha"] = BASE
            elif contradiction == "dirty_build":
                value["dirty"] = True
            elif contradiction == "runtime_extra":
                (Path(value["runtime_root"]) / "unqualified-code.py").write_text("unverified")
            else:
                value["runtime_files"] = []
        rewrite_reference(artifact["build"], change_build)
    elif contradiction in {"headless_smoke", "short_window", "wrong_origin", "embedded_source", "raw_egress", "missing_process_exit", "wrong_exec"}:
        def change_smoke(value):
            if contradiction == "headless_smoke":
                value["display_backend"] = "offscreen"
            elif contradiction == "short_window":
                value["first_launch_observation_seconds"] = 1
            elif contradiction in {"wrong_origin", "embedded_source"}:
                def change_diagnostic(diagnostic):
                    if contradiction == "wrong_origin":
                        diagnostic["events"][0]["module_origin"] = "/checkout/src/imagesorter/__init__.py"
                    else:
                        diagnostic["build_identity"]["source_sha"] = BASE
                rewrite_reference(value["diagnostic"], change_diagnostic)
            else:
                trace = value["traces"][0]
                target = Path(trace["path"])
                data = target.read_text()
                if contradiction == "raw_egress":
                    data = '101.0 socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) = -1 EPERM\n' + data
                elif contradiction == "missing_process_exit":
                    data = data.replace("+++ exited with 0 +++", "+++ killed by SIGKILL +++")
                else:
                    data = data.replace(artifact["path"], "/another-artifact")
                target.write_text(data)
                trace["sha256"] = file_digest(target)
        rewrite_reference(artifact["smoke"], change_smoke)
    else:
        case_id = {"missing_mutation": "KDE-05", "undrained_mutator": "KDE-14",
                   "component_swap": "COMPONENT:ai.mobilenet-v2", "missing_raw_format": "COMPONENT:codec.camera-raw",
                   "gpu_enumeration_only": "COMPONENT:provider.onnx-nvidia"}.get(contradiction, "KDE-03")
        case = next(row for row in receipt["cases"] if row["id"] == case_id and row["artifact_kind"] == artifact["kind"])
        def change_observation(value):
            if contradiction == "anonymous_case":
                value["operator"] = ""
            elif contradiction == "untested_criterion":
                value["criteria"]["no_stale_frame"] = False
            elif contradiction == "missing_mutation":
                value["mutation_receipts"] = []
            elif contradiction == "undrained_mutator":
                value["mutation_drain_completed"] = False
            elif contradiction == "component_swap":
                value["component_manifest_sha256"] = "0" * 64
            elif contradiction == "missing_raw_format":
                value["formats"].pop("nef")
            else:
                rewrite_reference(value["gpu_result"], lambda gpu: gpu.update(cuda_compute_events=0, node_providers=[]))
        rewrite_reference(case["observation"], change_observation)
    path.write_text(json.dumps(receipt))
    with pytest.raises(GateError, match="semantics"):
        verify_qualification(path, CANDIDATE, TREE, COMPONENT_IDS)


@pytest.mark.parametrize("change", ["changed", "duplicate"])
def test_component_receipt_cannot_grant_trust_outside_installed_catalogue(tmp_path, change):
    path, receipt = qualified_manifest(tmp_path)
    artifact = receipt["artifacts"][0]
    def edit_build(build):
        catalog_item = next(item for item in build["runtime_files"] if item["relative_path"].endswith("component_catalog.json"))
        catalog_path = Path(build["runtime_root"]) / catalog_item["relative_path"]
        catalog = json.loads(catalog_path.read_text())
        if change == "changed":
            catalog["components"][0]["sha256"] = "0" * 64
        else:
            catalog["components"].append(catalog["components"][0])
        catalog_path.write_text(json.dumps(catalog))
        catalog_item["sha256"] = file_digest(catalog_path)
    rewrite_reference(artifact["build"], edit_build)
    path.write_text(json.dumps(receipt))
    with pytest.raises(GateError, match="not trusted"):
        verify_qualification(path, CANDIDATE, TREE, COMPONENT_IDS)
