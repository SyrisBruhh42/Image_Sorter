"""Source-only GitHub integration. Binary/native publication remains separate.

Only explicit run/resume --execute may mutate GitHub. Every original branch and
PR is retained. This companion deliberately does not invoke the release gates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

if __package__:
    from . import cutover as core
else:
    import cutover as core

MODE = "source-integration-preserve"
MAIN = "refs/heads/main"
INTEGRATION = "refs/heads/integration/unified-main"
REQUIRED_CHECKS = {"source-tests", "lint", "dependencies", "package-smoke", "independent-review"}
PHASES = ("prepare", "archive", "stage", "qualify", "merge", "verify-merged",
          "switch-default", "finalize", "rollback-default")


def local_evidence(path, candidate, tree):
    value = core.load_json(path)
    core.require(value.get("kind") == "source-integration-evidence" and
                 value.get("source_sha") == candidate and value.get("source_tree") == tree and
                 value.get("result") == "passed", "Local source evidence identity/result differs")
    core.require(isinstance(value.get("reviewers"), list) and value["reviewers"] and
                 all(isinstance(item, str) and item.strip() for item in value["reviewers"]),
                 "Independent review attribution is required")
    checks = value.get("checks")
    core.require(isinstance(checks, list) and checks, "Local source checks are required")
    core.require(REQUIRED_CHECKS <= {check.get("name") for check in checks}, "Required source check categories missing")
    core.require(isinstance(value.get("pr_body"), str) and len(value["pr_body"].strip()) >= 120,
                 "A substantive reviewed PR body is required")
    for check in checks:
        core.require(check.get("name") and check.get("status") == "passed" and check.get("evidence"),
                     "Local source check is missing or did not pass")
        for entry in check["evidence"]:
            core.checked_file(entry, Path(path).parent)
    return value


def inspect(backend):
    result = backend.snapshot()
    result["tags"] = backend.remote_refs("tags")
    return result


def make_plan(backend, inventory, bundle, evidence, run_id, previous_plan=None, previous_root=None):
    core.require(inventory.get("schema_version") == 1 and inventory["repository"] == backend.repository,
                 "Inventory repository/schema differs")
    core.require(re.fullmatch(r"[A-Za-z0-9_-]+", run_id), "Invalid run ID")
    core.require(inventory["can_administer"] and inventory["allow_merge_commit"],
                 "Repository admin access and merge commits are required")
    continuation = None
    if previous_plan:
        core.require(previous_root is not None, "Continuation requires previous evidence root")
        previous_plan = Path(previous_plan).resolve()
        previous = core.load_json(previous_plan)
        prior = SourceController(backend, previous, previous_root)
        core.require(run_id != previous["run_id"], "Continuation needs a new immutable run ID")
        prior.check_state()
        prior.check_policy()
        pr = prior.integration_pr()
        core.require(pr and not pr.get("merged") and pr["state"] == "open", "Only unmerged owned PRs can continue")
        core.require(backend.remote_refs().get(MAIN) == previous["base_sha"] and
                     backend.remote_refs().get(INTEGRATION) == previous["candidate_sha"], "Owned staged refs drifted")
        core.require(backend.ancestor(previous["candidate_sha"], inventory["candidate_sha"]) and
                     previous["candidate_sha"] != inventory["candidate_sha"], "Repair must be a strict normal fast-forward")
        core.require(not prior.journal.last("merge", "intent"), "Merge intent must be reconciled before continuation")
        core.require(inventory["repository_id"] == previous["inventory"]["repository_id"] and
                     inventory["actor"]["id"] == previous["inventory"]["actor"]["id"], "Continuation identity changed")
        chain = {"plan": {"path": str(previous_plan), "sha256": core.file_digest(previous_plan)},
                 "journal": {"path": str(prior.journal.path), "sha256": core.file_digest(prior.journal.path)},
                 "plan_sha256": core.digest(previous), "last_event_sha256": prior.journal.events[-1]["event_sha256"]}
        continuation = {"chain": chain, "chain_sha256": core.digest(chain),
                        "previous_sha": previous["candidate_sha"], "pr_number": pr["number"],
                        "previous_pr_body": pr.get("body") or "", "marker": prior.marker(),
                        "policy": prior.observed_rule(), "policy_name": prior.initial_rule()["name"]}
        inventory = dict(inventory, branches=previous["inventory"]["branches"],
                         prs=previous["inventory"]["prs"], default_ref=previous["base_ref"],
                         tags=backend.remote_refs("tags"))
    else:
        core.require(not inventory["rulesets"] and not inventory["default_protected"],
                     "Pre-existing protection requires a compatibility plan")
    core.require(inventory["delete_branch_on_merge"] is False, "Automatic branch deletion must already be disabled")
    branches = inventory["branches"]
    core.require(MAIN not in branches and INTEGRATION not in branches, "Unexpected main/integration branch exists")
    old = core.ref(inventory["default_ref"])
    candidate = core.sha(inventory["candidate_sha"])
    base = core.sha(branches[old])
    core.require(backend.ancestor(base, candidate), "Candidate does not contain the original default")
    core.require(candidate != base, "Candidate has no source integration to merge")
    tree = core.sha(backend.tree(candidate))
    local = local_evidence(evidence, candidate, tree)
    bundle = Path(bundle).resolve()
    core.require(bundle.is_file(), "Recovery bundle is missing")
    backend.git("bundle", "verify", str(bundle))
    archives = [core.make_tag(backend, run_id, core.sha(oid), "branch-" + core.digest(name)[:12])
                for name, oid in sorted(branches.items())]
    for pr in inventory["prs"]:
        archives.append(core.make_tag(backend, run_id, core.sha(pr["head_sha"]), f"pr-{pr['number']}"))
    archives.append(core.make_tag(backend, run_id, candidate, "candidate"))
    core.require(all(a["ref"] not in inventory.get("tags", {}) for a in archives), "Archive namespace already exists")
    return {"schema_version": 1, "operation": "source-integration", "mode": MODE, "run_id": run_id, "created_at": core.utc(),
            "inventory": inventory, "inventory_sha256": core.digest(inventory),
            "candidate_sha": candidate, "candidate_tree": tree, "base_sha": base,
            "base_ref": old, "main_ref": MAIN, "integration_ref": INTEGRATION,
            "archives": archives, "bundle": {"path": str(bundle), "sha256": core.file_digest(bundle)},
            "local_evidence": {"path": str(Path(evidence).resolve()), "sha256": core.file_digest(evidence)},
            "phases": list(PHASES), "binary_publication": "not-performed", "release_complete": False,
            "continuation": continuation, "pr_body": local["pr_body"],
            "allowed_mutations": ["create-annotated-source-archives", "create-main-at-base", "create-owned-integration",
                                  "fast-forward-owned-integration-if-continuation", "create-or-update-owned-pr-body",
                                  "main-only-protection", "exact-head-merge", "default-metadata"],
            "prohibited_mutations": ["modify-original-branches", "force-update", "delete-refs", "rename-refs",
                                     "close-historical-prs", "native-success-attestation", "binary-publication", "release-publication"]}


class SourceController:
    # Reuse audited pure identity/CI/journal/archive operations, without inheriting
    # any executable native-publication, rename, delete, or PR-closure phases.
    action = core.Controller.action
    endpoint = core.Controller.endpoint
    remote_ref = core.Controller.remote_ref
    required_ci = core.Controller.required_ci
    ci_evidence = core.Controller.ci_evidence
    ensure_tags = core.Controller.ensure_tags
    restore = core.Controller.restore
    archive_gate = core.Controller.archive_gate
    archive = core.Controller.archive
    live_rules = core.Controller.live_rules

    def __init__(self, backend, plan, root, merged_validation=None):
        self.backend, self.plan, self.root = backend, plan, Path(root).resolve()
        self.merged_validation = merged_validation
        core.require(plan.get("schema_version") == 1 and plan.get("mode") == MODE and
                     plan.get("operation") == "source-integration",
                     "Not a source-only preservation plan")
        core.require(plan.get("phases") == list(PHASES), "Unexpected source-only phases")
        core.require(plan["inventory_sha256"] == core.digest(plan["inventory"]), "Inventory changed")
        core.require(plan["inventory"]["repository"] == backend.repository, "Repository differs")
        checkout = Path(backend.checkout).resolve()
        core.require(self.root != checkout and checkout not in self.root.parents,
                     "Evidence directory must be outside the checkout")
        self.journal = core.Journal(self.root, plan)

    def identity(self):
        core.require(core.digest(self.plan) == self.journal.plan_hash, "Plan changed")
        self.backend.validate_origin()
        repo = self.backend.api(self.endpoint())
        actor = self.backend.api("user")
        core.require(repo["id"] == self.plan["inventory"]["repository_id"] and
                     actor["id"] == self.plan["inventory"]["actor"]["id"], "Repository/operator identity changed")
        core.require(repo.get("permissions", {}).get("admin") is True and repo.get("allow_merge_commit") is True,
                     "Admin access or merge method changed")
        core.require(repo.get("delete_branch_on_merge") is False, "Automatic deletion must remain disabled")
        core.require(self.backend.git("rev-parse", "HEAD") == self.plan["candidate_sha"] and
                     self.backend.tree(self.plan["candidate_sha"]) == self.plan["candidate_tree"], "Candidate changed")
        core.checked_file(self.plan["bundle"], self.root)
        evidence = core.checked_file(self.plan["local_evidence"], self.root)
        local = local_evidence(evidence, self.plan["candidate_sha"], self.plan["candidate_tree"])
        core.require(local["pr_body"] == self.plan["pr_body"], "Reviewed PR body differs")
        continuation = self.plan.get("continuation")
        if continuation:
            chain = continuation["chain"]
            core.require(core.digest(chain) == continuation["chain_sha256"], "Continuation chain changed")
            old_plan = core.checked_file(chain["plan"], self.root)
            old_journal = core.checked_file(chain["journal"], self.root)
            core.require(core.digest(core.load_json(old_plan)) == chain["plan_sha256"], "Previous plan identity changed")
            last = json.loads(old_journal.read_text().splitlines()[-1])
            core.require(last["event_sha256"] == chain["last_event_sha256"], "Previous journal chain changed")
        return repo

    def integration_pr(self):
        rows = [p for p in self.backend.prs() if p["head_ref"] == INTEGRATION[11:] and
                p["base_ref"] == "main" and p["head_repository_id"] == self.plan["inventory"]["repository_id"]]
        core.require(len(rows) <= 1, "Ambiguous integration PR")
        if not rows:
            core.require(not self.plan.get("continuation") and
                         not self.journal.last("integration-pr", "result") and
                         not self.journal.last("integration-pr", "reconciled"), "Owned integration PR disappeared or changed base")
            return None
        continuation = self.plan.get("continuation")
        core.require(self.journal.last("integration-pr", "intent") or continuation, "Unrecorded integration PR")
        pr = self.backend.pr(rows[0]["number"])
        allowed_heads = {self.plan["candidate_sha"]}
        if continuation and not self.journal.last("fast-forward-integration", "result") and not self.journal.last("fast-forward-integration", "reconciled"):
            allowed_heads.add(continuation["previous_sha"])
        core.require(pr["head"]["sha"] in allowed_heads and self.marker() in (pr.get("body") or "") and
                     (not continuation or pr["number"] == continuation["pr_number"]),
                     "Integration PR identity changed")
        allowed_bodies = {self.reviewed_body()}
        if continuation and not self.journal.last("integration-pr", "reconciled"):
            allowed_bodies = {continuation["previous_pr_body"]}
            if self.journal.last("integration-pr-body", "intent"):
                allowed_bodies.add(self.reviewed_body())
        core.require((pr.get("body") or "") in allowed_bodies, "Reviewed integration PR body changed")
        return pr

    def reviewed_body(self):
        return (f"{self.marker()}\n\n{self.plan['pr_body'].strip()}\n\n"
                f"Exact source: `{self.plan['candidate_sha']}`; tree: `{self.plan['candidate_tree']}`.\n"
                "Source integration only. All original branches and PRs are retained. "
                "Binary/component publication is not performed; native acceptance and release qualification remain separate.\n")

    def marker(self):
        continuation = self.plan.get("continuation")
        return continuation["marker"] if continuation else f"<!-- imagesorter-source-integration:{self.plan['run_id']} -->"

    def merged_identity(self):
        pr = self.integration_pr()
        core.require(pr and pr.get("merged") and self.journal.last("merge", "intent"), "Unrecorded/unmerged integration")
        merged = core.sha(pr["merge_commit_sha"])
        self.backend.git("fetch", "--no-tags", "origin", merged)
        parents = self.backend.git("show", "-s", "--format=%P", merged).split()
        core.require(parents == [self.plan["base_sha"], self.plan["candidate_sha"]], "Merge parents differ from [B,C]")
        core.require(self.backend.tree(merged) == self.plan["candidate_tree"], "Merged tree differs from candidate")
        return merged

    def check_state(self):
        original = self.plan["inventory"]["branches"]
        expected = dict(original)
        continuation = self.plan.get("continuation")
        if continuation:
            expected[MAIN] = self.plan["base_sha"]
            current = self.remote_ref(INTEGRATION)
            allowed = {continuation["previous_sha"]}
            if self.journal.last("fast-forward-integration", "intent"):
                allowed.add(self.plan["candidate_sha"])
            core.require(current in allowed, "Owned integration drift during continuation")
            expected[INTEGRATION] = current
        pr = self.integration_pr()
        if continuation and pr and pr.get("merged"):
            expected[MAIN] = self.merged_identity()
        if self.journal.last("create-main", "intent"):
            main = self.remote_ref(MAIN)
            if main is not None:
                expected[MAIN] = self.merged_identity() if pr and pr.get("merged") else self.plan["base_sha"]
        if self.journal.last("create-integration", "intent") and self.remote_ref(INTEGRATION) is not None:
            expected[INTEGRATION] = self.plan["candidate_sha"]
        core.require(self.backend.remote_refs() == expected, "Branch drift: original refs or planned main/integration changed")
        known = {p["number"]: p for p in self.plan["inventory"]["prs"]}
        current = {p["number"]: p for p in self.backend.prs() if not pr or p["number"] != pr["number"]}
        core.require(set(current) == set(known), "Original PR inventory changed")
        for number, old in known.items():
            observed = current[number]
            if observed == old:
                continue
            stable = {key: val for key, val in old.items() if key not in {"state", "merged_at"}}
            core.require(all(observed.get(key) == val for key, val in stable.items()), "Original PR head/base drift")
            core.require(pr and pr.get("merged") and old["state"] == "open" and
                         observed["state"] == "closed" and observed.get("merged_at"), "Original PR inventory changed")
            actual = self.backend.pr(number)
            core.require(actual.get("merged") is True and
                         self.backend.ancestor(old["head_sha"], self.merged_identity()),
                         "Historical PR status change lacks GitHub merge/ancestry evidence")
            operation = f"github-pr-observation:{number}"
            if not self.journal.last(operation, "observed"):
                self.journal.append(operation, "observed", before=old, after=observed,
                                    attribution="GitHub-reported; no controller close request")
        tags = self.backend.remote_refs("tags")
        expected_tags = dict(self.plan["inventory"].get("tags", {}))
        if self.journal.last("archive-publish", "intent"):
            for item in self.plan["archives"]:
                if item["ref"] in tags:
                    expected_tags[item["ref"]] = item["tag_sha"]
                    expected_tags[item["ref"] + "^{}"] = item["commit_sha"]
        core.require(tags == expected_tags, "Tag inventory drift")
        default = self.backend.api(self.endpoint())["default_branch"]
        allowed = {self.plan["base_ref"][11:]}
        if self.journal.last("switch-default", "intent"):
            allowed.add("main")
        core.require(default in allowed, "Unexpected default branch")

    def initial_rule(self):
        continuation = self.plan.get("continuation")
        name = continuation["policy_name"] if continuation else f"Image Sorter source {self.plan['run_id']} main"
        return {"name": name, "target": "branch",
                "enforcement": "active", "bypass_actors": [],
                "conditions": {"ref_name": {"include": [MAIN], "exclude": []}},
                "rules": [{"type": "non_fast_forward"}, {"type": "deletion"}]}

    def quality_rule(self, app_id):
        rule = self.initial_rule()
        rule["rules"].extend([
            {"type": "pull_request", "parameters": {"allowed_merge_methods": ["merge"],
                "dismiss_stale_reviews_on_push": True, "require_code_owner_review": False,
                "require_last_push_approval": False, "required_approving_review_count": 0,
                "required_review_thread_resolution": True}},
            {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [{"context": core.CI_CONTEXT, "integration_id": int(app_id)}]}}])
        return rule

    def check_policy(self):
        rows = self.live_rules()
        intent = self.journal.last("main-policy", "intent")
        quality = self.journal.last("source-quality", "intent")
        continuation = self.plan.get("continuation")
        allowed = [self.initial_rule()]
        if continuation:
            allowed.append(continuation["policy"])
        if quality:
            allowed.append(quality["desired"])
        if not rows:
            core.require(not continuation, "Inherited main protection disappeared")
            core.require(not self.journal.last("main-policy", "result") and
                         not self.journal.last("main-policy", "reconciled"), "Main protection disappeared")
            return
        core.require((intent or continuation) and len(rows) == 1 and any(core.contains(rows[0], body) for body in allowed),
                     "Main protection or ruleset inventory changed")
        if self.journal.last("source-quality", "result") or self.journal.last("source-quality", "reconciled"):
            core.require(core.contains(rows[0], quality["desired"]), "Strict source quality policy changed")

    def prepare(self):
        # Verify the supplied recovery bundle by restoring its refs independently.
        if not self.journal.last("input-bundle", "result"):
            directory = Path(tempfile.mkdtemp(prefix="source-bundle-restore-", dir=self.root))
            self.backend.command(["git", "init", "--bare", str(directory)])
            self.backend.command(["git", "-C", str(directory), "fetch", str(self.plan["bundle"]["path"]), "+refs/*:refs/*"], timeout=600)
            self.backend.command(["git", "-C", str(directory), "fsck", "--full", "--strict"], timeout=600)
            oids = set(self.plan["inventory"]["branches"].values()) | {self.plan["candidate_sha"]}
            oids.update(pr["head_sha"] for pr in self.plan["inventory"]["prs"])
            for oid in oids:
                self.backend.command(["git", "-C", str(directory), "cat-file", "-e", f"{oid}^{{commit}}"])
            self.journal.append("input-bundle", "result", restored=str(directory))
        if self.plan.get("continuation"):
            core.require(core.contains(self.observed_rule(), self.plan["continuation"]["policy"]), "Inherited main policy drift")
            self.journal.append("main-policy", "reconciled", observed=self.observed_rule(), inherited=True)
            return
        wanted = self.initial_rule()
        self.action("main-policy", None, wanted,
                    lambda: self.backend.api(self.endpoint("/rulesets"), "POST", wanted),
                    lambda: self.observed_rule())

    def observed_rule(self):
        rows = self.live_rules()
        core.require(len(rows) <= 1, "Unexpected ruleset inventory")
        if not rows:
            return None
        actual = core.Controller.rule_body(rows[0])
        quality = self.journal.last("source-quality", "intent")
        if quality and core.contains(actual, quality["desired"]):
            return quality["desired"]
        continuation = self.plan.get("continuation")
        if continuation and core.contains(actual, continuation["policy"]):
            return continuation["policy"]
        if core.contains(actual, self.initial_rule()):
            return self.initial_rule()
        return actual

    def create_ref(self, name, oid, operation):
        self.action(operation, None, oid,
                    lambda: self.backend.git("-c", "push.followTags=false", "push", "--porcelain",
                        f"--force-with-lease={name}:", "origin", f"{oid}:{name}"),
                    lambda: self.remote_ref(name))

    def stage(self):
        self.archive_gate(self.plan["candidate_sha"])
        continuation = self.plan.get("continuation")
        if continuation:
            previous = continuation["previous_sha"]
            core.require(self.backend.ancestor(previous, self.plan["candidate_sha"]), "Repair is not a fast-forward")
            self.action("fast-forward-integration", previous, self.plan["candidate_sha"],
                        lambda: self.backend.git("-c", "push.followTags=false", "push", "--porcelain", "origin",
                                                f"{self.plan['candidate_sha']}:{INTEGRATION}"),
                        lambda: self.remote_ref(INTEGRATION))
        else:
            self.create_ref(MAIN, self.plan["base_sha"], "create-main")
            self.create_ref(INTEGRATION, self.plan["candidate_sha"], "create-integration")
        body = self.reviewed_body()
        pr = self.integration_pr()
        if pr:
            if continuation:
                self.action("integration-pr-body", continuation["previous_pr_body"], body,
                            lambda: self.backend.api(self.endpoint(f"/pulls/{pr['number']}"), "PATCH", {"body": body}),
                            lambda: self.backend.pr(pr["number"]).get("body") or "")
            core.require((self.backend.pr(pr["number"]).get("body") or "") == body, "Reviewed integration PR body changed")
            self.journal.append("integration-pr", "reconciled", number=pr["number"], url=pr["html_url"])
            return
        self.journal.intent("integration-pr", expected=None, desired={"head": INTEGRATION, "base": MAIN})
        pr = self.backend.api(self.endpoint("/pulls"), "POST", {
            "title": "Integrate complete Image Sorter source while preserving branch history",
            "head": INTEGRATION[11:], "base": "main", "body": body, "draft": False})
        self.journal.append("integration-pr", "result", number=pr["number"], url=pr["html_url"])

    def source_ci(self, oid, sources):
        check = self.required_ci(oid)
        evidence = self.ci_evidence(check, sources)
        return {"id": check["id"], "url": check["html_url"], "app_id": check["app"]["id"], "evidence": evidence}

    def qualify(self):
        self.archive_gate(self.plan["candidate_sha"])
        pr = self.integration_pr()
        core.require(pr and pr["head"]["sha"] == self.plan["candidate_sha"] and
                     pr["state"] == "open" and not pr.get("merged") and pr.get("mergeable") is True,
                     "An open, mergeable integration PR is required")
        core.require(self.remote_ref(MAIN) == self.plan["base_sha"] and pr["base"]["sha"] == self.plan["base_sha"],
                     "Main/base advanced")
        test_merge = core.sha(pr["merge_commit_sha"])
        self.backend.git("fetch", "--no-tags", "origin", f"refs/pull/{pr['number']}/merge")
        core.require(self.backend.tree(test_merge) == self.plan["candidate_tree"], "Test merge tree differs")
        ci = self.source_ci(self.plan["candidate_sha"], {self.plan["candidate_sha"], test_merge})
        wanted = self.quality_rule(ci["app_id"])
        rows = self.live_rules()
        core.require(len(rows) == 1, "Main policy is missing")
        initial = self.plan["continuation"]["policy"] if self.plan.get("continuation") else self.initial_rule()
        self.action("source-quality", initial, wanted,
                    lambda: self.backend.api(self.endpoint(f"/rulesets/{rows[0]['id']}"), "PUT", wanted),
                    self.observed_rule)
        self.journal.append("source-qualified", "result", source_sha=self.plan["candidate_sha"],
                            source_tree=self.plan["candidate_tree"], test_merge_sha=test_merge, ci=ci)

    def merge(self):
        record = self.journal.last("source-qualified", "result")
        core.require(record, "Source qualification receipt missing")
        pr = self.integration_pr()
        core.require(pr, "Integration PR missing")
        if pr.get("merged"):
            self.journal.append("merge", "reconciled", merge_sha=self.merged_identity(), attribution="not-inferred")
            return
        core.require(pr["state"] == "open" and pr.get("mergeable") is True and
                     pr["base"]["sha"] == self.plan["base_sha"] and
                     pr["merge_commit_sha"] == record["test_merge_sha"], "PR/base/test merge changed")
        self.source_ci(self.plan["candidate_sha"], {self.plan["candidate_sha"], record["test_merge_sha"]})
        core.require(core.contains(self.observed_rule(), self.quality_rule(record["ci"]["app_id"])),
                     "Strict source CI policy missing")
        self.journal.intent("merge", expected={"base": self.plan["base_sha"], "head": self.plan["candidate_sha"]}, desired="merge-commit")
        response = self.backend.api(self.endpoint(f"/pulls/{pr['number']}/merge"), "PUT",
                                    {"sha": self.plan["candidate_sha"], "merge_method": "merge"})
        core.require(response.get("merged") is True, "GitHub did not merge")
        merged = self.merged_identity()
        core.require(merged == response["sha"], "Merge response and readback differ")
        self.journal.append("merge", "result", merge_sha=merged)

    def verify_local_merge(self, path, merged):
        core.require(path is not None, "Supply --merged-validation for an independent fresh merge checkout")
        path = Path(path).resolve()
        value = core.load_json(path)
        core.require(value.get("kind") == "source-merged-validation" and value.get("operation") == "source-integration" and
                     value.get("source_sha") == merged and value.get("source_tree") == self.plan["candidate_tree"] and
                     value.get("result") == "passed" and value.get("fresh_checkout") is True,
                     "Independent merge validation identity/result differs")
        checks = value.get("checks") or []
        core.require(REQUIRED_CHECKS <= {c.get("name") for c in checks}, "Independent merge check categories missing")
        for check in checks:
            core.require(check.get("status") == "passed" and check.get("evidence"), "Independent merge check did not pass")
            for entry in check["evidence"]:
                core.checked_file(entry, path.parent)
        checkout = Path(value.get("checkout_path", "")).resolve()
        core.require(checkout.is_dir() and checkout != Path(self.backend.checkout).resolve(), "Independent checkout is required")
        def git_at(directory, *args):
            return self.backend.command(["git", "-C", str(directory), *args])
        core.require(git_at(checkout, "rev-parse", "HEAD") == merged and
                     git_at(checkout, "rev-parse", "HEAD^{tree}") == self.plan["candidate_tree"] and
                     not git_at(checkout, "status", "--porcelain"), "Independent checkout changed or is dirty")
        core.require(git_at(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir") !=
                     git_at(self.backend.checkout, "rev-parse", "--path-format=absolute", "--git-common-dir"),
                     "Independent validation must use a separate Git object repository")
        return {"path": str(path), "sha256": core.file_digest(path)}

    def verify_merged(self):
        merged = self.merged_identity()
        core.require(self.remote_ref(MAIN) == merged, "Main changed after merge")
        ci = self.source_ci(merged, {merged})
        validation = self.verify_local_merge(self.merged_validation, merged)
        self.journal.append("merged-source-qualified", "result", source_sha=merged,
                            source_tree=self.plan["candidate_tree"], ci=ci, local_validation=validation)

    def merged_gate(self):
        merged = self.merged_identity()
        record = self.journal.last("merged-source-qualified", "result")
        core.require(record and record["source_sha"] == merged, "Actual merge CI receipt missing")
        core.checked_file(record["ci"]["evidence"], self.root)
        validation = core.checked_file(record["local_validation"], self.root)
        self.verify_local_merge(validation, merged)
        self.source_ci(merged, {merged})
        core.require(self.remote_ref(MAIN) == merged, "Main advanced")
        return merged

    def switch_default(self):
        merged = self.merged_gate()
        self.action("switch-default", self.plan["base_ref"][11:], "main",
                    lambda: self.backend.api(self.endpoint(), "PATCH", {"default_branch": "main"}),
                    lambda: self.backend.api(self.endpoint())["default_branch"])
        self.journal.append("default-verified", "result", source_sha=merged)

    def finalize(self):
        merged = self.merged_gate()
        core.require(self.backend.api(self.endpoint())["default_branch"] == "main", "Main is not default")
        self.archive_gate(self.plan["candidate_sha"])
        self.check_state()
        self.check_policy()
        self.journal.append("complete", "result", source_sha=merged, source_tree=self.plan["candidate_tree"],
                            remote_branches=self.backend.remote_refs(), original_refs_preserved=True,
                            binary_publication="not-performed", release_complete=False)

    def rollback_default(self):
        core.require(self.journal.last("switch-default", "intent"), "No recorded default switch to roll back")
        merged = self.merged_identity()
        core.require(self.remote_ref(MAIN) == merged, "Main drift prevents automatic rollback")
        self.action("rollback-default", "main", self.plan["base_ref"][11:],
                    lambda: self.backend.api(self.endpoint(), "PATCH", {"default_branch": self.plan["base_ref"][11:]}),
                    lambda: self.backend.api(self.endpoint())["default_branch"])
        self.journal.append("rolled-back", "result", retained_main=merged, old_default=self.plan["base_ref"])

    def run(self, phase):
        core.require(phase in PHASES, "Forbidden source-only phase")
        self.identity()
        self.check_state()
        self.check_policy()
        if phase != "prepare":
            core.require(self.journal.last("main-policy", "result") or self.journal.last("main-policy", "reconciled"),
                         "Prepare must finish first")
        getattr(self, phase.replace("-", "_"))()
        self.check_state()
        self.check_policy()

    def receipt(self):
        complete = self.journal.last("complete", "result")
        rollback = self.journal.last("rolled-back", "result")
        return {"schema_version": 1, "operation": "source-integration", "mode": MODE, "run_id": self.plan["run_id"],
                "plan_sha256": core.digest(self.plan), "inventory_sha256": self.plan["inventory_sha256"],
                "complete": bool(complete and (not rollback or complete["sequence"] > rollback["sequence"])),
                "binary_publication": "not-performed", "release_complete": False,
                "native_qualification": "not-asserted", "events": self.journal.events}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "plan", "run", "resume", "receipt"))
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default="SyrisBruhh42/Image_Sorter")
    for name in ("output", "inventory", "bundle", "local-evidence", "plan", "evidence-root", "merged-validation", "continue-plan", "continue-evidence-root"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        backend = core.Backend(args.checkout, args.repository)
        if args.command == "inspect":
            result = inspect(backend)
        elif args.command == "plan":
            core.require(all((args.inventory, args.bundle, args.local_evidence, args.run_id)), "Plan inputs missing")
            result = make_plan(backend, core.load_json(args.inventory), args.bundle, args.local_evidence, args.run_id,
                               args.continue_plan, args.continue_evidence_root)
        else:
            core.require(args.plan and args.evidence_root, "Plan and evidence root required")
            plan = core.load_json(args.plan)
            if args.command != "receipt":
                core.require(args.plan_sha256 == core.digest(plan) and args.phase, "Exact reviewed plan hash and phase required")
            with core.exclusive(args.evidence_root):
                controller = SourceController(backend, plan, args.evidence_root, args.merged_validation)
                if args.execute and args.command in {"run", "resume"}:
                    controller.run(args.phase)
                result = controller.receipt()
                if args.command != "receipt" and not args.execute:
                    result.update(dry_run=True, selected_phase=args.phase)
        if args.output:
            core.immutable_json(args.output, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (core.GateError, KeyError, TypeError, OSError, ValueError) as exc:
        print(f"Source integration stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
