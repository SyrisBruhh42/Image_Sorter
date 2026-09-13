"""Source-only controller failure injection; no network or real repositories."""
import copy
import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts import cutover as core
from scripts import source_integration as source

REPAIR = "7" * 40
B, C, T, P, M = (str(i) * 40 for i in range(1, 6))
OLD = "refs/heads/feature/original"
TOPIC = "refs/heads/topic"


class FakeBackend:
    repository = "owner/repo"

    def __init__(self, checkout):
        self.checkout = checkout
        checkout.mkdir()
        self.heads = {OLD: B, TOPIC: B}
        self.tags = {}
        self.rules = []
        self.original_prs = [{"number": 1, "state": "open", "merged_at": None,
            "head_sha": B, "head_ref": "topic", "head_repository_id": 123,
            "base_ref": OLD[11:], "url": "https://github.com/owner/repo/pull/1"}]
        self.pull = None
        self.default = OLD[11:]
        self.candidate = C
        self.independent_head = M
        self.shared_git = False
        self.writes = []
        self.lose = None
        self.parents = [B, C]
        self.trees = {B: "6" * 40, C: T, P: T, M: T}
        self.ci_conclusion = "success"
        self.ci_source = {}
        self.ci_jobs = {name: {"result": "success"} for name in core.CI_JOBS}
        self.check_app = {"id": 9, "slug": "github-actions"}

    def endpoint(self, suffix=""):
        return "repos/owner/repo" + suffix

    def validate_origin(self):
        pass

    def remote_refs(self, namespace="heads"):
        return dict(self.tags if namespace == "tags" else self.heads)

    def tree(self, oid):
        return self.trees[oid]

    def ancestor(self, old, new):
        return (old == B and new in {C, REPAIR, M}) or (old == C and new in {REPAIR, M})

    def prs(self):
        rows = copy.deepcopy(self.original_prs)
        if self.pull:
            rows.append({"number": 99, "head_ref": source.INTEGRATION[11:], "base_ref": "main",
                         "head_repository_id": 123, "head_sha": self.pull["head"]["sha"],
                         "state": self.pull["state"], "merged_at": "now" if self.pull["merged"] else None})
        return rows

    def pr(self, number):
        if number != 99:
            return {"merged": True}
        return copy.deepcopy(self.pull)

    def snapshot(self):
        return {"schema_version": 1, "repository": self.repository, "repository_id": 123,
                "actor": {"id": 42, "login": "operator"}, "can_administer": True,
                "allow_merge_commit": True, "delete_branch_on_merge": False,
                "default_ref": OLD, "default_protected": False,
                "candidate_sha": self.candidate, "branches": dict(self.heads), "prs": self.prs(),
                "rulesets": [], "archives": [], "tags": {}}

    def lost(self, key):
        if self.lose == key:
            self.lose = None
            raise core.GateError("response lost")

    def git(self, *args, **kwargs):
        if args[:2] == ("rev-parse", "HEAD"):
            return self.candidate
        if args[:2] == ("var", "GIT_COMMITTER_IDENT"):
            return "Synthetic Test <test@example.invalid> 1 +0000"
        if args[:3] == ("show", "-s", "--format=%P"):
            return " ".join(self.parents)
        if "push" in args:
            oid, name = args[-1].split(":", 1)
            assert name in {source.MAIN, source.INTEGRATION}
            if name in self.heads:
                assert name == source.INTEGRATION and self.ancestor(self.heads[name], oid)
                assert not any("--force" in arg for arg in args)
                self.writes.append(("fast-forward-ref", name, oid))
                self.heads[name] = oid
                self.pull["head"]["sha"] = oid
                self.lost("fast-forward")
            else:
                assert f"--force-with-lease={name}:" in args
                self.writes.append(("create-ref", name, oid))
                self.heads[name] = oid
                self.lost("create-ref")
        return ""

    def command(self, args, **kwargs):
        if kwargs.get("binary"):
            run = 20 if "/20/zip" in args[-1] else 21
            oid = self.candidate if run == 20 else M
            receipt = {"schema_version": 1, "source_sha": self.ci_source.get(oid, P if oid == self.candidate else M),
                       "source_tree": self.trees[self.candidate], "check_head_sha": oid, "repository_id": 123,
                       "workflow_run_id": run, "run_attempt": 1, "jobs": self.ci_jobs}
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("cutover-ci.json", json.dumps(receipt))
            return stream.getvalue()
        if args[:2] == ["git", "-C"] and "rev-parse" in args:
            if args[-1] == "HEAD":
                return self.independent_head
            if args[-1] == "HEAD^{tree}":
                return self.trees[self.candidate]
            if args[-1] == "--git-common-dir":
                return str(self.checkout / ".git") if self.shared_git else str(Path(args[2]) / ".git")
        return ""

    def api(self, path, method="GET", body=None, paginate=False):
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(body)))
        if path == "user":
            return {"id": 42}
        if path == self.endpoint():
            if method == "PATCH":
                assert set(body) == {"default_branch"}
                self.default = body["default_branch"]
                self.lost("default")
            return {"id": 123, "permissions": {"admin": True}, "allow_merge_commit": True,
                    "delete_branch_on_merge": False, "default_branch": self.default}
        if "/rulesets" in path:
            if method == "POST":
                assert not self.rules
                self.rules = [{"id": 7, **copy.deepcopy(body)}]
                self.lost("policy")
            elif method == "PUT":
                self.rules = [{"id": 7, **copy.deepcopy(body)}]
                self.lost("quality")
            if path.endswith("/rulesets/7"):
                return copy.deepcopy(self.rules[0])
            return copy.deepcopy(self.rules)
        if path.endswith("/pulls") and method == "POST":
            assert body["base"] == "main"
            self.pull = {"number": 99, "state": "open", "merged": False, "mergeable": True,
                         "head": {"sha": self.candidate}, "base": {"sha": B}, "merge_commit_sha": P,
                         "html_url": "https://github.com/owner/repo/pull/99", "body": body["body"]}
            self.lost("pr")
            return self.pr(99)
        if path.endswith("/pulls/99") and method == "PATCH":
            assert set(body) == {"body"}
            self.pull["body"] = body["body"]
            self.lost("pr-body")
            return self.pr(99)
        if path.endswith("/pulls/99/merge"):
            assert method == "PUT" and body == {"sha": self.candidate, "merge_method": "merge"}
            self.pull.update(merged=True, state="closed", merge_commit_sha=M)
            self.heads[source.MAIN] = M
            self.lost("merge")
            return {"merged": True, "sha": M}
        if "/check-runs?" in path:
            oid = M if M in path else self.candidate
            return [{"id": 10 if oid == self.candidate else 11, "name": core.CI_CONTEXT, "head_sha": oid,
                     "status": "completed", "conclusion": self.ci_conclusion,
                     "app": self.check_app, "check_suite": {"id": 20 if oid == self.candidate else 21},
                     "html_url": "https://github.com/owner/repo/actions/runs/20"}]
        if "/actions/runs?" in path:
            run = 20 if "check_suite_id=20" in path else 21
            return [{"id": run, "head_sha": self.candidate if run == 20 else M, "check_suite_id": run,
                     "status": "completed", "conclusion": "success", "path": ".github/workflows/ci.yml",
                     "run_attempt": 1}]
        if "/artifacts?" in path:
            run = 20 if "/runs/20/" in path else 21
            return [{"id": run, "name": f"cutover-ci-{run}-1", "expired": False}]
        raise AssertionError((method, path, body))


def file_ref(path, content):
    path.write_text(content)
    return {"path": str(path), "sha256": core.file_digest(path)}


@pytest.fixture
def setup(tmp_path):
    backend = FakeBackend(tmp_path / "checkout")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    log = file_ref(evidence_dir / "review.txt", "Synthetic passing review/test fixture; not product evidence.")
    evidence = evidence_dir / "local.json"
    evidence.write_text(json.dumps({"kind": "source-integration-evidence", "source_sha": C,
        "source_tree": T, "result": "passed", "reviewers": ["Synthetic reviewer"],
        "pr_body": "Synthetic test fixture source integration: include the complete tested behavior, preserve history, and retain independent review evidence. Validation uses mocked source controller tests only.",
        "checks": [{"name": name, "status": "passed", "evidence": [log]} for name in source.REQUIRED_CHECKS]}))
    bundle = evidence_dir / "recovery.bundle"
    bundle.write_text("synthetic bundle")
    plan = source.make_plan(backend, backend.snapshot(), bundle, evidence, "test")
    independent = tmp_path / "independent"
    independent.mkdir()
    validation = evidence_dir / "merged.json"
    validation.write_text(json.dumps({"kind": "source-merged-validation", "operation": "source-integration",
        "source_sha": M, "source_tree": T, "result": "passed", "fresh_checkout": True,
        "checkout_path": str(independent),
        "checks": [{"name": name, "status": "passed", "evidence": [log]} for name in source.REQUIRED_CHECKS]}))
    controller = source.SourceController(backend, plan, evidence_dir / "run", validation)
    return backend, controller


def fake_archive(backend, controller):
    core.immutable_json(controller.root / "archive-receipt.json", {"synthetic": True})
    bundle = controller.plan["bundle"]
    controller.journal.append("archive-local", "result", bundle=bundle)
    controller.journal.intent("archive-publish", expected="absent", desired=controller.plan["archives"])
    for item in controller.plan["archives"]:
        backend.tags[item["ref"]] = item["tag_sha"]
        backend.tags[item["ref"] + "^{}"] = item["commit_sha"]
    controller.journal.append("archive-publish", "result", archives=controller.plan["archives"])


def staged(backend, controller):
    controller.run("prepare")
    fake_archive(backend, controller)
    controller.run("stage")


def qualified(backend, controller):
    staged(backend, controller)
    controller.run("qualify")


def test_complete_source_route_preserves_refs_prs_and_never_publishes(setup):
    backend, controller = setup
    original_refs = dict(backend.heads)
    original_prs = copy.deepcopy(backend.original_prs)
    qualified(backend, controller)
    controller.run("merge")
    assert backend.default == OLD[11:]
    controller.run("verify-merged")
    controller.run("switch-default")
    controller.run("finalize")
    assert backend.heads == {**original_refs, source.MAIN: M, source.INTEGRATION: C}
    assert backend.original_prs == original_prs
    assert backend.default == "main"
    receipt = controller.receipt()
    assert receipt["complete"] and not receipt["release_complete"]
    assert receipt["native_qualification"] == "not-asserted"
    assert receipt["binary_publication"] == "not-performed"
    assert len(backend.rules) == 1
    rule = backend.rules[0]
    assert rule["conditions"]["ref_name"]["include"] == [source.MAIN]
    assert rule["bypass_actors"] == []
    assert rule["rules"][2]["parameters"]["required_approving_review_count"] == 0
    assert all("releases" not in str(write) and "/statuses/" not in str(write) and
               write[0] != "DELETE" for write in backend.writes)


@pytest.mark.parametrize("name,oid", [(source.MAIN, B), (TOPIC, C), ("refs/heads/unknown", C)])
def test_unexpected_or_changed_branch_stops_before_remote_write(setup, name, oid):
    backend, controller = setup
    backend.heads[name] = oid
    with pytest.raises(core.GateError, match="Branch drift"):
        controller.run("prepare")
    assert backend.writes == []


def test_missing_original_branch_stops(setup):
    backend, controller = setup
    del backend.heads[TOPIC]
    with pytest.raises(core.GateError, match="Branch drift"):
        controller.run("prepare")


def test_original_pr_change_stops(setup):
    backend, controller = setup
    backend.original_prs[0]["state"] = "closed"
    with pytest.raises(core.GateError, match="PR inventory"):
        controller.run("prepare")


@pytest.mark.parametrize("lost", ["policy", "create-ref", "pr", "quality", "merge", "default"])
def test_uncertain_success_reconciles_without_duplicate_write(setup, lost):
    backend, controller = setup
    if lost == "policy":
        phase = "prepare"
    else:
        controller.run("prepare")
        fake_archive(backend, controller)
        phase = "stage"
        if lost not in {"create-ref", "pr"}:
            controller.run("stage")
            phase = "qualify"
            if lost not in {"quality"}:
                controller.run("qualify")
                phase = "merge"
                if lost != "merge":
                    controller.run("merge")
                    controller.run("verify-merged")
                    phase = "switch-default"
    backend.lose = lost
    with pytest.raises(core.GateError, match="response lost"):
        controller.run(phase)
    before = len(backend.writes)
    resumed = source.SourceController(backend, controller.plan, controller.root, controller.merged_validation)
    resumed.run(phase)
    # A lost first ref creation resumes by creating the remaining ref and PR.
    allowance = 2 if lost == "create-ref" else 0
    assert len(backend.writes) == before + allowance


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "neutral", None])
def test_no_nonpassing_ci_qualifies(setup, conclusion):
    backend, controller = setup
    staged(backend, controller)
    backend.ci_conclusion = conclusion
    with pytest.raises(core.GateError, match="did not succeed"):
        controller.run("qualify")
    assert not controller.journal.last("source-qualified")


def test_ci_from_other_app_rejected(setup):
    backend, controller = setup
    staged(backend, controller)
    backend.check_app = {"id": 9, "slug": "untrusted"}
    with pytest.raises(core.GateError, match="GitHub Actions"):
        controller.run("qualify")


@pytest.mark.parametrize("change", ["source", "jobs"])
def test_wrong_ci_source_or_skipped_dependency_rejected(setup, change):
    backend, controller = setup
    staged(backend, controller)
    if change == "source":
        backend.ci_source[C] = B
    else:
        backend.ci_jobs["quality"]["result"] = "skipped"
    with pytest.raises(core.GateError, match="identity differs|skipped"):
        controller.run("qualify")


@pytest.mark.parametrize("parents", [[C, B], [B], [B, P]])
def test_actual_merge_wrong_parents_never_switches_default(setup, parents):
    backend, controller = setup
    qualified(backend, controller)
    backend.parents = parents
    with pytest.raises(core.GateError, match="Merge parents"):
        controller.run("merge")
    assert backend.default == OLD[11:]


def test_actual_merge_wrong_tree_never_switches_default(setup):
    backend, controller = setup
    qualified(backend, controller)
    backend.trees[M] = B
    with pytest.raises(core.GateError, match="Merged tree"):
        controller.run("merge")
    assert backend.default == OLD[11:]


def test_test_merge_wrong_tree_blocks_qualification(setup):
    backend, controller = setup
    staged(backend, controller)
    backend.trees[P] = B
    with pytest.raises(core.GateError, match="Test merge tree"):
        controller.run("qualify")


def test_default_cannot_switch_on_candidate_ci_only(setup):
    backend, controller = setup
    qualified(backend, controller)
    controller.run("merge")
    with pytest.raises(core.GateError, match="Actual merge CI"):
        controller.run("switch-default")
    backend.ci_source[M] = C
    with pytest.raises(core.GateError, match="identity differs"):
        controller.run("verify-merged")
    assert backend.default == OLD[11:]


def test_rollback_changes_only_default_and_marks_receipt_incomplete(setup):
    backend, controller = setup
    qualified(backend, controller)
    for phase in ("merge", "verify-merged", "switch-default", "finalize"):
        controller.run(phase)
    before_refs, before_prs = dict(backend.heads), copy.deepcopy(backend.original_prs)
    count = len(backend.writes)
    controller.run("rollback-default")
    assert backend.default == OLD[11:]
    assert backend.heads == before_refs and backend.original_prs == before_prs
    assert backend.writes[count:] == [("PATCH", backend.endpoint(), {"default_branch": OLD[11:]})]
    assert not controller.receipt()["complete"]


def test_policy_drift_and_source_evidence_tamper_block(setup):
    backend, controller = setup
    staged(backend, controller)
    backend.rules[0]["bypass_actors"] = [{"actor_id": 42}]
    with pytest.raises(core.GateError, match="protection"):
        controller.run("qualify")
    backend.rules[0]["bypass_actors"] = []
    Path(controller.plan["local_evidence"]["path"]).write_text("{}")
    with pytest.raises(core.GateError, match="hash mismatch"):
        controller.run("qualify")


@pytest.mark.parametrize("phase", ["rename", "prune", "resolve", "release"])
def test_out_of_scope_phases_forbidden(setup, phase):
    backend, controller = setup
    with pytest.raises(core.GateError, match="Forbidden"):
        controller.run(phase)
    assert not backend.writes


def continuation(backend, controller, *, qualified_before=False):
    (qualified if qualified_before else staged)(backend, controller)
    previous_file = controller.root.parent / "previous-plan.json"
    core.immutable_json(previous_file, controller.plan)
    backend.candidate = REPAIR
    backend.trees[REPAIR] = T
    evidence = json.loads(Path(controller.plan["local_evidence"]["path"]).read_text())
    evidence["source_sha"] = REPAIR
    evidence["pr_body"] += " This revision repairs the failed hosted qualification and repeats source verification."
    next_evidence = controller.root.parent / "repaired-evidence.json"
    next_evidence.write_text(json.dumps(evidence))
    inventory = backend.snapshot()
    inventory["rulesets"] = copy.deepcopy(backend.rules)
    plan = source.make_plan(backend, inventory, controller.plan["bundle"]["path"], next_evidence,
                            "repair", previous_file, controller.root)
    return source.SourceController(backend, plan, controller.root.parent / "repair-run", controller.merged_validation)


@pytest.mark.parametrize("qualified_before", [False, True])
def test_bounded_continuation_only_fast_forwards_owned_source_and_requalifies(setup, qualified_before):
    backend, old = setup
    new = continuation(backend, old, qualified_before=qualified_before)
    count = len(backend.writes)
    original = old.plan["inventory"]["branches"]
    old_events = old.journal.path.read_bytes()
    new.run("prepare")
    fake_archive(backend, new)
    new.run("stage")
    assert backend.heads == {**original, source.MAIN: B, source.INTEGRATION: REPAIR}
    writes = backend.writes[count:]
    assert writes[0] == ("fast-forward-ref", source.INTEGRATION, REPAIR)
    assert writes[1][0] == "PATCH" and writes[1][1].endswith("/pulls/99")
    assert not new.journal.last("source-qualified")
    with pytest.raises(core.GateError, match="qualification"):
        new.run("merge")
    new.run("qualify")
    backend.parents = [B, REPAIR]
    new.run("merge")
    new.run("verify-merged")
    new.run("switch-default")
    new.run("finalize")
    assert backend.heads == {**original, source.MAIN: M, source.INTEGRATION: REPAIR}
    assert old.journal.path.read_bytes() == old_events
    assert new.plan["continuation"]["chain_sha256"]
    assert new.receipt()["operation"] == "source-integration"


@pytest.mark.parametrize("lost", ["fast-forward", "pr-body"])
def test_uncertain_continuation_ff_and_body_update_reconcile(setup, lost):
    backend, old = setup
    new = continuation(backend, old)
    new.run("prepare")
    fake_archive(backend, new)
    backend.lose = lost
    with pytest.raises(core.GateError, match="response lost"):
        new.run("stage")
    before = len(backend.writes)
    resumed = source.SourceController(backend, new.plan, new.root, new.merged_validation)
    resumed.run("stage")
    assert len(backend.writes) == before + (1 if lost == "fast-forward" else 0)
    assert backend.heads[source.INTEGRATION] == REPAIR


def test_continuation_chain_or_owned_state_drift_blocks(setup):
    backend, old = setup
    new = continuation(backend, old)
    old.journal.append("external-action", "observed", note="old execution resumed")
    with pytest.raises(core.GateError, match="hash mismatch"):
        new.run("prepare")


def test_unrelated_repair_history_is_rejected(setup):
    backend, old = setup
    staged(backend, old)
    prior = old.root.parent / "prior.json"
    core.immutable_json(prior, old.plan)
    backend.ancestor = lambda old, new: False
    with pytest.raises(core.GateError, match="fast-forward"):
        source.make_plan(backend, backend.snapshot(), old.plan["bundle"]["path"],
                         old.plan["local_evidence"]["path"], "repair", prior, old.root)


def test_continuation_after_merge_is_rejected(setup):
    backend, old = setup
    qualified(backend, old)
    old.run("merge")
    prior = old.root.parent / "prior.json"
    core.immutable_json(prior, old.plan)
    with pytest.raises(core.GateError, match="unmerged"):
        source.make_plan(backend, backend.snapshot(), old.plan["bundle"]["path"],
                         old.plan["local_evidence"]["path"], "repair", prior, old.root)


def test_all_original_pr_heads_have_annotated_archives_even_if_not_branch_tips(setup):
    backend, old = setup
    inventory = backend.snapshot()
    orphan = "8" * 40
    inventory["prs"][0]["head_sha"] = orphan
    plan = source.make_plan(backend, inventory, old.plan["bundle"]["path"],
                            old.plan["local_evidence"]["path"], "orphan")
    assert any(a["commit_sha"] == orphan and "/pr-1-" in a["ref"] for a in plan["archives"])


def test_github_reported_historical_merge_observed_without_close_call(setup):
    backend, controller = setup
    qualified(backend, controller)
    controller.run("merge")
    backend.original_prs[0].update(state="closed", merged_at="GitHub timestamp")
    before = len(backend.writes)
    controller.run("verify-merged")
    assert len(backend.writes) == before
    assert controller.journal.last("github-pr-observation:1", "observed")
    backend.original_prs[0]["head_sha"] = C
    with pytest.raises(core.GateError, match="head/base drift"):
        controller.run("switch-default")


@pytest.mark.parametrize("problem", ["missing", "wrong-head", "same-git", "tamper", "checks"])
def test_independent_merge_validation_is_required_and_exact(setup, problem):
    backend, controller = setup
    qualified(backend, controller)
    controller.run("merge")
    if problem == "missing":
        controller.merged_validation = None
    elif problem == "wrong-head":
        backend.independent_head = C
    elif problem == "same-git":
        backend.shared_git = True
    else:
        value = json.loads(Path(controller.merged_validation).read_text())
        if problem == "tamper":
            value["source_sha"] = C
        else:
            value["checks"] = []
        Path(controller.merged_validation).write_text(json.dumps(value))
    with pytest.raises(core.GateError):
        controller.run("verify-merged")
    assert backend.default == OLD[11:]
    assert not controller.journal.last("merged-source-qualified")


def test_missing_source_check_category_and_trivial_pr_body_rejected(setup):
    backend, controller = setup
    evidence = Path(controller.plan["local_evidence"]["path"])
    original = json.loads(evidence.read_text())
    changed = copy.deepcopy(original)
    changed["checks"] = changed["checks"][:1]
    evidence.write_text(json.dumps(changed))
    with pytest.raises(core.GateError, match="categories"):
        source.local_evidence(evidence, C, T)
    original["pr_body"] = "ok"
    evidence.write_text(json.dumps(original))
    with pytest.raises(core.GateError, match="PR body"):
        source.local_evidence(evidence, C, T)


def test_reviewed_pr_body_drift_blocks_qualification(setup):
    backend, controller = setup
    staged(backend, controller)
    backend.pull["body"] += "unreviewed change"
    with pytest.raises(core.GateError, match="PR body"):
        controller.run("qualify")


def test_reviewed_pr_body_drift_after_qualification_blocks_merge_before_write(setup):
    backend, controller = setup
    qualified(backend, controller)
    backend.pull["body"] += "unreviewed change"
    before = len(backend.writes)
    with pytest.raises(core.GateError, match="PR body"):
        controller.run("merge")
    assert len(backend.writes) == before


def test_continuation_previous_body_drift_stops_before_fast_forward(setup):
    backend, old = setup
    new = continuation(backend, old)
    backend.pull["body"] += "unreviewed change"
    before = len(backend.writes)
    with pytest.raises(core.GateError, match="PR body"):
        new.run("prepare")
    assert len(backend.writes) == before
