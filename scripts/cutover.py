"""Evidence-gated GitHub cutover. No remote writes without ``run --execute``.

Inventory and plan files are immutable. Local journal intents are fsynced before
remote requests; unknown outcomes are reconciled from GitHub before any retry.
This is an operator tool, not part of the application runtime.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

SCHEMA = 1
CI_CONTEXT = "Image Sorter required CI"
NATIVE_CONTEXT = "cutover/qualified"
CI_JOBS = {"quality", "x11-smoke", "native-portability", "linux-artifacts", "optional-ai-integration"}
COMPONENT_IDS = {
    "codec.heif-avif", "codec.camera-raw", "viewer.animation-multipage",
    "ai.mobilenet-v2", "provider.onnx-nvidia",
}
PHASES = (
    "prepare", "archive", "publish", "qualify", "merge", "verify-merged",
    "rename", "resolve", "prune", "finalize",
)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class GateError(RuntimeError):
    """Evidence or remote state does not permit the requested operation."""


def require(condition, message):
    if not condition:
        raise GateError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha(value):
    require(isinstance(value, str) and HEX40.fullmatch(value), "A full lowercase Git SHA is required")
    return value


def ref(value, namespace="refs/heads/"):
    require(isinstance(value, str) and value.startswith(namespace), f"Expected a full {namespace} ref")
    require(not any(c in value for c in " ~^:?*[\\\x00\n\r\t"), "Invalid ref name")
    require(".." not in value and "@{" not in value and "//" not in value, "Invalid ref name")
    require(all(p and not p.startswith(".") and not p.endswith((".", ".lock"))
                for p in value.split("/")), "Invalid ref name")
    return value


def fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def immutable_json(path, value):
    """Create, never overwrite; tolerate an exact retry after interruption."""
    path = Path(path)
    data = canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == data, f"Refusing to overwrite immutable file: {path}")
        return
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_dir(path.parent)


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, OSError) as exc:
        raise GateError(f"Cannot read JSON evidence {path}: {exc}") from exc


class Journal:
    def __init__(self, root, plan):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "events.jsonl"
        self.plan_hash = digest(plan)
        self.events = self._read()
        for event in self.events:
            require(event["plan_sha256"] == self.plan_hash, "Journal belongs to a different plan")

    def _read(self):
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        events, good_bytes, previous = [], 0, None
        lines = raw.splitlines(keepends=True)
        for index, line in enumerate(lines):
            try:
                require(line.endswith(b"\n"), "Incomplete final journal write")
                event = json.loads(line)
            except (ValueError, GateError) as exc:
                require(index == len(lines) - 1, f"Corrupt journal before its final line: {exc}")
                # The durable intent still governs any ambiguous remote outcome.
                damaged = self.root / f"events.torn-{uuid.uuid4().hex}"
                with damaged.open("xb") as stream:
                    stream.write(raw[good_bytes:])
                    stream.flush()
                    os.fsync(stream.fileno())
                fd, temporary = tempfile.mkstemp(prefix=".events-recovered-", dir=self.root)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw[:good_bytes])
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                fsync_dir(self.root)
                break
            claimed = event.pop("event_sha256")
            require(claimed == digest(event), "Journal hash mismatch")
            require(event["previous_sha256"] == previous, "Journal hash chain mismatch")
            require(event["sequence"] == len(events) + 1, "Journal sequence mismatch")
            event["event_sha256"] = claimed
            previous = claimed
            events.append(event)
            good_bytes += len(line)
        return events

    def append(self, operation, stage, **details):
        event = {
            "sequence": len(self.events) + 1, "utc": utc(), "operation": operation,
            "stage": stage, "plan_sha256": self.plan_hash,
            "previous_sha256": self.events[-1]["event_sha256"] if self.events else None,
            **details,
        }
        event["event_sha256"] = digest(event)
        with self.path.open("ab") as stream:
            stream.write(canonical(event) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        fsync_dir(self.root)
        self.events.append(event)
        return event

    def last(self, operation, stage=None):
        return next((e for e in reversed(self.events) if e["operation"] == operation
                     and (stage is None or e["stage"] == stage)), None)

    def intent(self, operation, **details):
        if not self.last(operation, "intent"):
            self.append(operation, "intent", **details)


@contextlib.contextmanager
def exclusive(root):
    # Production cutover is Linux-only; application portability is unaffected.
    import fcntl

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "controller.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GateError("Another controller is running in this evidence directory") from exc
        yield


class Backend:
    def __init__(self, checkout, repository):
        self.checkout = Path(checkout).resolve()
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "Invalid GitHub repository")
        self.repository = repository

    def command(self, args, *, input=None, timeout=120, cwd=None, binary=False):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1", GH_HOST="github.com")
        env.pop("GH_DEBUG", None)
        try:
            result = subprocess.run(args, input=input, text=not binary, capture_output=True,
                                    cwd=cwd or self.checkout, env=env, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateError(f"Command outcome unavailable ({args[0]}): {exc}") from exc
        if result.returncode:
            # Never include environment variables or authorization headers.
            raise GateError(f"Command failed ({args[0]}, exit {result.returncode}): {result.stderr[-4000:]}")
        return result.stdout if binary else result.stdout.strip()

    def git(self, *args, **kwargs):
        return self.command(["git", *args], **kwargs)

    def api(self, path, method="GET", body=None, paginate=False):
        args = ["gh", "api", "--hostname", "github.com", "--method", method,
                "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2026-03-10", path]
        if paginate:
            args.extend(["--paginate", "--slurp"])
        if body is not None:
            args.extend(["--input", "-"])
        output = self.command(args, input=json.dumps(body) if body is not None else None)
        value = json.loads(output) if output else None
        if isinstance(paginate, str):
            return [item for page in value for item in page[paginate]]
        return [item for page in value for item in page] if paginate else value

    def endpoint(self, suffix=""):
        return f"repos/{self.repository}{suffix}"

    def validate_origin(self):
        expected = {
            f"https://github.com/{self.repository}.git", f"https://github.com/{self.repository}",
            f"git@github.com:{self.repository}.git", f"ssh://git@github.com/{self.repository}.git",
        }
        fetch_urls = self.git("remote", "get-url", "--all", "origin").splitlines()
        push_urls = self.git("remote", "get-url", "--push", "--all", "origin").splitlines()
        require(len(fetch_urls) == 1 and fetch_urls[0] in expected, "Origin URL does not match the authorized repository")
        require(len(push_urls) == 1 and push_urls[0] in expected, "Origin has an unexpected or additional push destination")
        require(self.git("rev-parse", "--is-shallow-repository") == "false", "A full object repository is required")
        require(not self.git("status", "--porcelain"), "Candidate checkout has uncommitted changes")

    def remote_refs(self, namespace="heads"):
        output = self.git("ls-remote", f"--{namespace}", "origin")
        return {name: oid for oid, name in (line.split("\t") for line in output.splitlines())}

    def tree(self, oid):
        return self.git("rev-parse", f"{sha(oid)}^{{tree}}")

    def ancestor(self, older, newer):
        try:
            self.git("merge-base", "--is-ancestor", sha(older), sha(newer))
            return True
        except GateError:
            return False

    def prs(self):
        rows = self.api(self.endpoint("/pulls?state=all&per_page=100"), paginate=True)
        return [{
            "number": row["number"], "state": row["state"], "merged_at": row.get("merged_at"),
            "head_sha": row["head"]["sha"], "head_ref": row["head"]["ref"],
            "head_repository_id": (row["head"].get("repo") or {}).get("id"),
            "base_ref": row["base"]["ref"], "url": row["html_url"],
        } for row in rows]

    def pr(self, number):
        return self.api(self.endpoint(f"/pulls/{int(number)}"))

    def local_archives(self):
        output = self.git("for-each-ref", "--format=%(refname) %(objectname) %(objecttype) %(*objectname)",
                          "refs/tags/archive/")
        entries = []
        for line in output.splitlines():
            parts = line.split()
            require(len(parts) == 4 and parts[2] == "tag", "Archive refs must be annotated commit tags")
            entries.append({"ref": ref(parts[0], "refs/tags/archive/"), "tag_sha": sha(parts[1]),
                            "commit_sha": sha(parts[3])})
        return entries

    def snapshot(self):
        self.validate_origin()
        repo = self.api(self.endpoint())
        actor = self.api("user")
        rules = self.api(self.endpoint("/rulesets?includes_parents=true&per_page=100"), paginate=True)
        full_rules = [self.api(self.endpoint(f"/rulesets/{r['id']}")) for r in rules]
        return {
            "schema_version": SCHEMA, "captured_at": utc(), "repository": self.repository,
            "repository_id": repo["id"], "default_ref": f"refs/heads/{repo['default_branch']}",
            "actor": {"id": actor["id"], "login": actor["login"]},
            "can_administer": repo.get("permissions", {}).get("admin") is True,
            "delete_branch_on_merge": repo.get("delete_branch_on_merge", False),
            "allow_merge_commit": repo.get("allow_merge_commit", False),
            "default_protected": self.api(self.endpoint(f"/branches/{quote(repo['default_branch'], safe='')}"))["protected"],
            "branches": self.remote_refs(), "prs": self.prs(), "rulesets": full_rules,
            "archives": self.local_archives(), "candidate_sha": self.git("rev-parse", "HEAD"),
        }


def make_tag(backend, run_id, oid, role, identity=None):
    name = f"archive/cutover/{run_id}/{role}-{sha(oid)}"
    identity = identity or backend.git("var", "GIT_COMMITTER_IDENT")
    body = f"object {oid}\ntype commit\ntag {name}\ntagger {identity}\n\nImage Sorter cutover {run_id}: {role}.\n"
    data = body.encode()
    tag_sha = hashlib.sha1(f"tag {len(data)}\0".encode() + data).hexdigest()
    return {"ref": f"refs/tags/{name}", "tag_sha": tag_sha, "commit_sha": oid, "tag_body": body}


def make_plan(backend, inventory, bundle, dispositions, run_id):
    require(inventory["schema_version"] == SCHEMA, "Unsupported inventory version")
    require(re.fullmatch(r"[a-zA-Z0-9_-]+", run_id), "Invalid run ID")
    require(inventory["repository"] == backend.repository, "Inventory repository mismatch")
    candidate = sha(inventory["candidate_sha"])
    baseline_ref = ref(inventory["default_ref"])
    require(baseline_ref != "refs/heads/main", "This migration requires the original named default; inspect completed cutover separately")
    require("refs/heads/main" not in inventory["branches"], "main already exists; collision requires review")
    baseline = sha(inventory["branches"][baseline_ref])
    require(backend.ancestor(baseline, candidate), "Candidate does not contain the current base")
    require(inventory["can_administer"], "Repository administration permission is required")
    require(inventory["allow_merge_commit"], "Repository must permit merge commits")
    require(not inventory["default_protected"], "Existing classic branch protection requires a compatibility review")
    require(not inventory["rulesets"], "Existing rulesets require an explicitly reviewed compatibility plan")
    archives = inventory["archives"] + [make_tag(backend, run_id, candidate, "candidate")]
    archived = {a["commit_sha"] for a in archives}
    for name, oid in inventory["branches"].items():
        ref(name)
        require(sha(oid) in archived, f"Branch lacks an archive: {name}")
    require(Path(bundle).is_file(), "Recovery bundle is missing")
    mapped = {int(row["number"]): row for row in dispositions}
    require(len(mapped) == len(dispositions), "Duplicate PR disposition")
    for pr in inventory["prs"]:
        if not pr["merged_at"]:
            require(pr["number"] in mapped, f"Missing disposition for PR #{pr['number']}")
    for row in dispositions:
        require(int(row["number"]) in {p["number"] for p in inventory["prs"]}, "Disposition references an uninventoried PR")
        require(row["disposition"] in {"integrated", "superseded", "already_closed"}, "Invalid PR disposition")
        require(bool(row.get("rationale")), "Disposition requires a specific rationale")
        require(bool(row.get("evidence")), "Disposition requires evidence file hashes")
    return {
        "schema_version": SCHEMA, "run_id": run_id, "created_at": utc(),
        "inventory": inventory, "inventory_sha256": digest(inventory),
        "candidate_sha": candidate, "candidate_tree": backend.tree(candidate),
        "base_ref": baseline_ref, "base_sha": baseline, "integration_ref": "refs/heads/integration/unified-main",
        "archives": archives, "dispositions": dispositions,
        "bundle": {"path": str(Path(bundle).resolve()), "sha256": file_digest(bundle)},
        "required_ci_context": CI_CONTEXT, "required_component_ids": sorted(COMPONENT_IDS),
        "phases": list(PHASES),
    }


def checked_file(entry, root):
    require(isinstance(entry, dict) and HEX64.fullmatch(entry.get("sha256", "")), "Missing evidence SHA-256")
    path = Path(entry.get("path", ""))
    if not path.is_absolute():
        path = Path(root) / path
    require(path.is_file(), f"Missing evidence file: {path}")
    require(file_digest(path) == entry["sha256"], f"Evidence hash mismatch: {path}")
    return path.resolve()


def verify_qualification(path, expected_sha, expected_tree, component_ids):
    """Verify receipt-backed evidence; never infer native success from CI."""
    path = Path(path)
    receipt = load_json(path)
    require(receipt.get("schema_version") == SCHEMA and receipt.get("complete") is True,
            "Qualification manifest is incomplete")
    require(receipt.get("source_sha") == expected_sha and receipt.get("source_tree") == expected_tree,
            "Qualification source identity mismatch")
    require(receipt.get("profile_id") == "linux-x86_64-full", "Full target qualification profile is required")
    require(receipt.get("display_backend") == "xcb", "Native X11 (xcb) evidence is required")
    require(receipt.get("session_type") == "x11" and receipt.get("desktop") == "KDE",
            "Actual KDE/X11 desktop evidence is required")
    require(receipt.get("native") is True, "Headless evidence cannot satisfy native qualification")
    required = {f"KDE-{i:02d}" for i in range(1, 15)}
    required |= {f"COMPONENT:{name}" for name in component_ids}
    artifacts = receipt.get("artifacts", [])
    require(len(artifacts) == 3, "Exactly three delivery artifact records are required")
    require({a.get("kind") for a in artifacts} == {"wheel", "onedir", "appimage"},
            "All three delivery artifacts require qualification")
    for artifact in artifacts:
        checked_file(artifact, path.parent)
    components = receipt.get("components", [])
    require(len(components) == len(component_ids), "Duplicate or missing component records")
    require({c.get("id") for c in components} == set(component_ids), "Required component profile is incomplete")
    for component in components:
        checked_file(component["manifest"], path.parent)
    cases = receipt.get("cases", [])
    for kind in ("wheel", "onedir", "appimage"):
        rows = [case for case in cases if case.get("artifact_kind") == kind]
        require(len({case["id"] for case in rows}) == len(rows), "Duplicate native test ID")
        by_id = {case["id"]: case for case in rows}
        require(required <= set(by_id), f"Missing native cases for {kind}: {sorted(required - set(by_id))}")
        for test_id in required:
            case = by_id[test_id]
            require(case.get("status") == "passed", f"Native case did not pass: {kind}/{test_id}")
            require(bool(case.get("evidence")), f"Native case has no evidence: {kind}/{test_id}")
            for evidence in case["evidence"]:
                checked_file(evidence, path.parent)
    require(receipt.get("all_processes_stopped") is True, "Native helpers or file operations remain active")
    require(receipt.get("base_first_launch_network_attempts") == 0,
            "Base first-launch egress-attempt evidence is missing or nonzero")
    try:
        if __package__:
            from .qualification_schema import verify_semantics
        else:
            from qualification_schema import verify_semantics
        verify_semantics(receipt, path.parent, expected_sha, expected_tree)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise GateError(f"Native evidence semantics failed: {exc}") from exc
    return receipt


class Controller:
    def __init__(self, backend, plan, root, qualification=None, distribution=None):
        self.backend, self.plan, self.root = backend, plan, Path(root)
        self.qualification = qualification
        self.distribution = distribution
        require(plan["schema_version"] == SCHEMA, "Unsupported plan schema")
        require(digest(plan["inventory"]) == plan["inventory_sha256"], "Inventory was modified")
        require(plan["inventory"]["repository"] == backend.repository, "Repository mismatch")
        self.journal = Journal(root, plan)

    def action(self, name, expected, desired, mutate, observe):
        self.journal.intent(name, expected=expected, desired=desired)
        current = observe()
        if current == desired:
            if not self.journal.last(name, "result"):
                self.journal.append(name, "reconciled", observed=current, attribution="not-inferred")
            return current
        require(current == expected, f"Unexpected state for {name}; no mutation performed")
        mutate()
        current = observe()
        require(current == desired, f"Outcome not yet verified for {name}; resume will reconcile")
        self.journal.append(name, "result", observed=current)
        return current

    def endpoint(self, suffix=""):
        return self.backend.endpoint(suffix)

    def remote_ref(self, name):
        namespace = "tags" if name.startswith("refs/tags/") else "heads"
        return self.backend.remote_refs(namespace).get(name)

    def identity(self):
        require(digest(self.plan) == self.journal.plan_hash, "In-memory plan changed after journal initialization")
        self.backend.validate_origin()
        repository = self.backend.api(self.endpoint())
        require(repository["id"] == self.plan["inventory"]["repository_id"], "Remote repository identity changed")
        actor = self.backend.api("user")
        require(actor["id"] == self.plan["inventory"]["actor"]["id"], "Authenticated cutover actor changed")
        require(repository.get("permissions", {}).get("admin") is True, "Administration permission was lost")
        require(self.backend.git("rev-parse", "HEAD") == self.plan["candidate_sha"], "Candidate checkout changed")
        checked_file(self.plan["bundle"], self.root)
        return repository

    def find_integration_pr(self):
        rows = [p for p in self.backend.prs() if p["head_ref"] == self.plan["integration_ref"][11:]
                and p["head_repository_id"] == self.plan["inventory"]["repository_id"]
                and p["base_ref"] in {self.plan["base_ref"][11:], "main"}]
        require(len(rows) <= 1, "Multiple integration PRs require reconciliation")
        return self.backend.pr(rows[0]["number"]) if rows else None

    def merge_identity(self):
        pr = self.find_integration_pr()
        require(pr and pr["merged"] and pr["head"]["sha"] == self.plan["candidate_sha"],
                "The qualified integration PR has not merged")
        merged = sha(pr["merge_commit_sha"])
        self.backend.git("fetch", "--no-tags", "origin", merged)
        require(self.backend.ancestor(self.plan["candidate_sha"], merged), "Merge omitted the qualified candidate")
        require(self.backend.tree(merged) == self.plan["candidate_tree"], "Merged tree differs from the qualified source")
        parents = self.backend.git("show", "-s", "--format=%P", merged).split()
        require(len(parents) == 2 and parents[1] == self.plan["candidate_sha"], "Expected a merge commit retaining the candidate parent")
        require(parents[0] == self.plan["base_sha"], "Merge first parent differs from the frozen base")
        require(self.backend.ancestor(parents[0], self.plan["candidate_sha"]), "Merge base was not contained in the qualified candidate")
        return merged

    def check_drift(self):
        heads = self.backend.remote_refs()
        originals = self.plan["inventory"]["branches"]
        pr = self.find_integration_pr()
        merged = self.merge_identity() if pr and pr.get("merged") else None
        expected = dict(originals)
        if merged:
            expected[self.plan["base_ref"]] = merged
            if "refs/heads/main" in heads:
                expected.pop(self.plan["base_ref"], None)
                expected["refs/heads/main"] = merged
        if self.plan["integration_ref"] in heads or self.journal.last("publish", "intent"):
            expected[self.plan["integration_ref"]] = self.plan["candidate_sha"]
        for name in list(expected):
            if name not in heads and self.journal.last(f"delete:{name}", "intent"):
                expected.pop(name)
        require(heads == expected, "Remote branches drifted from the inventory and recorded operations")
        known = {p["number"]: p for p in self.plan["inventory"]["prs"]}
        for current in self.backend.prs():
            if pr and current["number"] == pr["number"]:
                continue
            require(current["number"] in known, f"New PR #{current['number']} requires review")
            old = known[current["number"]]
            require(current["head_sha"] == old["head_sha"] and current["head_repository_id"] == old["head_repository_id"],
                    f"PR #{current['number']} head changed")
            allowed_bases = {old["base_ref"]}
            if merged and old["base_ref"] == self.plan["base_ref"][11:]:
                allowed_bases.add("main")
            require(current["base_ref"] in allowed_bases, f"PR #{current['number']} base changed")

    def desired_rules(self):
        prefix = f"Image Sorter cutover {self.plan['run_id']}"
        common = {"enforcement": "active", "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}}}
        quality = {
            "name": f"{prefix} quality", "target": "branch", "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"include": [self.plan["base_ref"], "refs/heads/main"], "exclude": []}},
            "rules": [
                {"type": "non_fast_forward"}, {"type": "deletion"},
                {"type": "pull_request", "parameters": {"allowed_merge_methods": ["merge"],
                    "dismiss_stale_reviews_on_push": True, "require_code_owner_review": False,
                    "require_last_push_approval": False, "required_approving_review_count": 0,
                    "required_review_thread_resolution": True}},
                {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": CI_CONTEXT}, {"context": NATIVE_CONTEXT}]}},
            ],
        }
        binding = self.journal.last("quality-app-binding", "result")
        if binding:
            quality["rules"][-1]["parameters"]["required_status_checks"][0] = binding["body"]["rules"][-1]["parameters"]["required_status_checks"][0]
        return [
            {**common, "name": f"{prefix} maintenance", "target": "branch",
             "bypass_actors": [{"actor_type": "User", "actor_id": self.plan["inventory"]["actor"]["id"], "bypass_mode": "always"}],
             "rules": [{"type": "creation"}, {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}}, {"type": "deletion"}]},
            quality,
            {"name": f"{prefix} archives", "target": "tag", "enforcement": "active", "bypass_actors": [],
             "conditions": {"ref_name": {"include": ["refs/tags/archive/**/*", "refs/tags/archive/*"], "exclude": []}},
             "rules": [{"type": "update", "parameters": {"update_allows_fetch_and_merge": False}}, {"type": "deletion"}]},
        ]

    @staticmethod
    def rule_body(row):
        return {key: row[key] for key in ("name", "target", "enforcement", "bypass_actors", "conditions", "rules")}

    def live_rules(self):
        rows = self.backend.api(self.endpoint("/rulesets?includes_parents=true&per_page=100"), paginate=True)
        return [self.backend.api(self.endpoint(f"/rulesets/{row['id']}")) for row in rows]

    def check_rules(self):
        require(self.backend.api(self.endpoint()).get("delete_branch_on_merge") is False,
                "Automatic branch deletion must remain disabled during cutover")
        live = self.live_rules()
        desired = self.desired_rules()
        require({r["name"] for r in live} == {r["name"] for r in desired}, "Ruleset inventory changed")
        for wanted in desired:
            actual = next(r for r in live if r["name"] == wanted["name"])
            # GitHub may add default optional fields; compare desired fields recursively.
            require(contains(actual, wanted), f"Cutover policy changed: {wanted['name']}")

    def prepare(self):
        self.check_drift()
        before = self.plan["inventory"]["delete_branch_on_merge"]
        self.action("disable-auto-delete", before, False,
                    lambda: self.backend.api(self.endpoint(), "PATCH", {"delete_branch_on_merge": False}),
                    lambda: self.backend.api(self.endpoint()).get("delete_branch_on_merge"))
        for wanted in self.desired_rules():
            existing = [r for r in self.live_rules() if r["name"] == wanted["name"]]
            require(len(existing) <= 1, "Duplicate controller rulesets")
            self.journal.intent(f"policy:{wanted['name']}", expected=None, desired=wanted)
            if existing:
                require(contains(existing[0], wanted), "Existing controller ruleset differs")
                self.journal.append(f"policy:{wanted['name']}", "reconciled", id=existing[0]["id"])
            else:
                created = self.backend.api(self.endpoint("/rulesets"), "POST", wanted)
                require(contains(created, wanted), "GitHub did not enforce the requested ruleset")
                self.journal.append(f"policy:{wanted['name']}", "result", id=created["id"])
        self.check_rules()

    def ensure_tags(self, archives):
        for item in archives:
            if item.get("tag_body"):
                oid = self.backend.git("mktag", input=item["tag_body"])
                require(oid == item["tag_sha"], "Generated tag-object identity mismatch")
                local = {a["ref"]: a for a in self.backend.local_archives()}.get(item["ref"])
                if local is None:
                    self.backend.git("update-ref", item["ref"], oid, "0" * 40)
            actual = self.backend.git("rev-parse", item["ref"])
            require(actual == item["tag_sha"], f"Local tag object differs: {item['ref']}")
            require(self.backend.git("rev-parse", f"{item['ref']}^{{commit}}") == item["commit_sha"], "Archive target differs")

    def restore(self, bundle, archives, remote=False):
        directory = Path(tempfile.mkdtemp(prefix="remote-restore-" if remote else "bundle-restore-", dir=self.root))
        self.backend.command(["git", "init", "--bare", str(directory)])
        source = self.backend.git("remote", "get-url", "origin") if remote else str(bundle)
        refspecs = [f"{a['ref']}:{a['ref']}" for a in archives]
        self.backend.command(["git", "-C", str(directory), "fetch", "--no-tags", source, *refspecs], timeout=600)
        self.backend.command(["git", "-C", str(directory), "fsck", "--full", "--strict"], timeout=600)
        for item in archives:
            oid = self.backend.command(["git", "-C", str(directory), "rev-parse", item["ref"]])
            peeled = self.backend.command(["git", "-C", str(directory), "rev-parse", f"{item['ref']}^{{commit}}"])
            require((oid, peeled) == (item["tag_sha"], item["commit_sha"]), "Restored archive identity differs")
        return str(directory)

    def archive(self):
        if self.journal.last("archive-publish", "result"):
            self.archive_gate()
            return
        archives = self.plan["archives"]
        self.ensure_tags(archives)
        bundle = self.root / "qualified-candidate.bundle"
        if not bundle.exists():
            temporary = self.root / "qualified-candidate.bundle.partial"
            if temporary.exists():
                os.replace(temporary, self.root / f"qualified-candidate.bundle.interrupted-{uuid.uuid4().hex}")
                fsync_dir(self.root)
            self.backend.git("bundle", "create", str(temporary), *(a["ref"] for a in archives), timeout=600)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, bundle)
            fsync_dir(self.root)
        self.backend.git("bundle", "verify", str(bundle))
        restored = self.restore(bundle, archives)
        self.journal.append("archive-local", "result", bundle={"path": str(bundle), "sha256": file_digest(bundle)}, restored=restored)
        remote = self.backend.remote_refs("tags")
        for item in archives:
            require(remote.get(item["ref"]) in {None, item["tag_sha"]}, "Existing remote archive tag differs")
            if item["ref"] in remote:
                require(remote.get(item["ref"] + "^{}") == item["commit_sha"], "Remote archive peeled target differs")
        self.journal.intent("archive-publish", expected="absent-or-identical", desired=archives)
        missing = [a for a in archives if a["ref"] not in remote]
        if missing:
            self.backend.git("-c", "push.followTags=false", "push", "--atomic", "origin",
                             *(f"{a['ref']}:{a['ref']}" for a in missing), timeout=600)
        restored_remote = self.restore(bundle, archives, remote=True)
        self.journal.append("archive-publish", "result", archives=archives, restored=restored_remote)

    def archive_gate(self, oid=None):
        evidence = self.journal.last("archive-publish", "result")
        require(evidence is not None, "Published archive restore evidence is missing")
        archives = evidence["archives"]
        remote = self.backend.remote_refs("tags")
        for item in archives:
            require(remote.get(item["ref"]) == item["tag_sha"] and remote.get(item["ref"] + "^{}") == item["commit_sha"],
                    "Published archives changed or disappeared")
        local = self.journal.last("archive-local", "result")
        require(local is not None, "Local recovery bundle evidence is missing")
        checked_file(local["bundle"], self.root)
        if oid:
            require(any(a["commit_sha"] == oid for a in archives), "Deletion target is not archived")

    def publish(self):
        self.archive_gate(self.plan["candidate_sha"])
        name, oid = self.plan["integration_ref"], self.plan["candidate_sha"]
        self.action("publish", None, oid,
                    lambda: self.backend.git("-c", "push.followTags=false", "push", "--porcelain",
                        f"--force-with-lease={name}:", "origin", f"{oid}:{name}"),
                    lambda: self.remote_ref(name))
        pr = self.find_integration_pr()
        marker = f"<!-- imagesorter-cutover:{self.plan['run_id']}:integration -->"
        if pr:
            require(pr["head"]["sha"] == oid and marker in (pr.get("body") or ""), "Existing PR is not this cutover's integration PR")
            self.journal.append("integration-pr", "reconciled", number=pr["number"], url=pr["html_url"])
            return
        body = f"{marker}\nUnified candidate `{oid}`.\n\nNative qualification: NOT YET VERIFIED.\n\nArchive namespace: `archive/`. Historical PR dispositions are recorded separately; ancestry is not a substitute for behavior evidence."
        self.journal.intent("integration-pr", expected=None, desired={"head": name, "sha": oid})
        pr = self.backend.api(self.endpoint("/pulls"), "POST", {"title": "Qualify and integrate Image Sorter unified candidate",
            "head": name[11:], "base": self.plan["base_ref"][11:], "body": body, "draft": False})
        self.journal.append("integration-pr", "result", number=pr["number"], url=pr["html_url"])

    def required_ci(self, oid):
        runs = self.backend.api(self.endpoint(f"/commits/{sha(oid)}/check-runs?filter=latest&check_name={quote(CI_CONTEXT)}&per_page=100"), paginate="check_runs")
        rows = [r for r in runs if r["name"] == CI_CONTEXT]
        require(bool(rows) and len({r.get("id") for r in rows}) == len(rows), "Missing or ambiguous aggregate CI identity")
        check = max(rows, key=lambda row: row.get("id", 0))
        require(check["head_sha"] == oid and check["status"] == "completed" and check["conclusion"] == "success",
                "Aggregate CI did not succeed on the exact checked commit")
        require(check.get("app", {}).get("slug") == "github-actions", "CI check was not produced by GitHub Actions")
        return check

    def ci_evidence(self, check, allowed_sources):
        suite = int(check["check_suite"]["id"])
        runs = self.backend.api(self.endpoint(f"/actions/runs?check_suite_id={suite}&per_page=100"), paginate="workflow_runs")
        runs = [run for run in runs if run["check_suite_id"] == suite]
        require(len(runs) == 1, "CI check cannot be tied to one workflow run")
        run = runs[0]
        require(run["head_sha"] == check["head_sha"] and run["status"] == "completed" and run["conclusion"] == "success",
                "Workflow run is not successful on the checked head")
        require(run["path"] == ".github/workflows/ci.yml", "Aggregate came from another workflow")
        name = f"cutover-ci-{run['id']}-{run['run_attempt']}"
        artifacts = self.backend.api(self.endpoint(f"/actions/runs/{run['id']}/artifacts?per_page=100"), paginate="artifacts")
        artifacts = [a for a in artifacts if a["name"] == name and not a["expired"]]
        require(len(artifacts) == 1, "Exact CI source evidence artifact is missing or ambiguous")
        raw = self.backend.command(["gh", "api", "--hostname", "github.com",
            self.endpoint(f"/actions/artifacts/{artifacts[0]['id']}/zip")], binary=True)
        require(len(raw) <= 1024 * 1024, "CI evidence archive is unexpectedly large")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                require(archive.namelist() == ["cutover-ci.json"], "Unexpected CI evidence archive contents")
                require(archive.getinfo("cutover-ci.json").file_size <= 1024 * 1024, "CI receipt is too large")
                evidence = json.loads(archive.read("cutover-ci.json"))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise GateError("CI evidence archive is unreadable") from exc
        require(evidence["schema_version"] == SCHEMA and evidence["source_sha"] in allowed_sources
                and evidence["source_tree"] == self.plan["candidate_tree"], "CI checkout identity differs from the qualified source")
        require(evidence["check_head_sha"] == check["head_sha"]
                and int(evidence["repository_id"]) == self.plan["inventory"]["repository_id"]
                and int(evidence["workflow_run_id"]) == run["id"]
                and int(evidence["run_attempt"]) == run["run_attempt"], "CI receipt provenance mismatch")
        require(set(evidence["jobs"]) == CI_JOBS and all(value["result"] == "success" for value in evidence["jobs"].values()),
                "CI receipt includes missing, skipped, or failed required jobs")
        path = self.root / f"ci-{run['id']}-{run['run_attempt']}.json"
        immutable_json(path, evidence)
        return {"path": str(path), "sha256": file_digest(path), "workflow_run_id": run["id"],
                "check_head_sha": check["head_sha"], "checkout_sha": evidence["source_sha"]}

    def native_gate(self, oid):
        require(self.qualification is not None, "Supply --qualification with complete native evidence")
        receipt = verify_qualification(self.qualification, oid, self.plan["candidate_tree"], self.plan["required_component_ids"])
        return {"manifest": {"path": str(Path(self.qualification).resolve()), "sha256": file_digest(self.qualification)},
                "source_sha": oid, "source_tree": receipt["source_tree"]}

    def set_native_status(self, oid, evidence):
        # This status is an attestation pointer, not a synthetic native test result.
        self.journal.intent(f"qualification-status:{oid}", expected="unqualified", desired=evidence)
        body = {
            "state": "success", "context": NATIVE_CONTEXT,
            "description": f"Receipt sha256 {evidence['manifest']['sha256'][:40]}",
        }
        statuses = self.backend.api(self.endpoint(f"/commits/{oid}/statuses?per_page=100"), paginate=True)
        latest = next((s for s in statuses if s["context"] == NATIVE_CONTEXT), None)
        if not (latest and contains(latest, body) and latest["creator"]["id"] == self.plan["inventory"]["actor"]["id"]):
            self.backend.api(self.endpoint(f"/statuses/{oid}"), "POST", body)
        self.journal.append(f"qualification-status:{oid}", "result", **evidence)

    def qualify(self):
        self.archive_gate(self.plan["candidate_sha"])
        evidence = self.native_gate(self.plan["candidate_sha"])
        pr = self.find_integration_pr()
        require(pr and not pr["merged"] and pr["state"] == "open", "An open integration PR is required")
        require(pr["head"]["sha"] == self.plan["candidate_sha"], "Integration PR head changed")
        base = self.remote_ref(self.plan["base_ref"])
        require(base == self.plan["base_sha"], "Base drift invalidates this qualification plan")
        require(pr.get("mergeable") is True and pr.get("merge_commit_sha"), "PR mergeability is not yet known or conflicts")
        test_merge = sha(pr["merge_commit_sha"])
        self.backend.git("fetch", "--no-tags", "origin", f"refs/pull/{pr['number']}/merge")
        require(self.backend.tree(test_merge) == self.plan["candidate_tree"], "PR test merge differs from the native-qualified tree")
        check = self.required_ci(self.plan["candidate_sha"])
        ci_evidence = self.ci_evidence(check, {self.plan["candidate_sha"], test_merge})
        # Bind the durable quality rule to the actual GitHub Actions integration.
        quality = self.desired_rules()[1]
        quality["rules"][-1]["parameters"]["required_status_checks"][0]["integration_id"] = check["app"]["id"]
        live = next(r for r in self.live_rules() if r["name"] == quality["name"])
        self.journal.intent("quality-app-binding", expected=self.rule_body(live), desired=quality)
        updated = self.backend.api(self.endpoint(f"/rulesets/{live['id']}"), "PUT", quality)
        require(contains(updated, quality), "Required CI source binding failed")
        self.journal.append("quality-app-binding", "result", id=live["id"], body=quality)
        self.set_native_status(self.plan["candidate_sha"], evidence)
        self.journal.append("qualified", "result", **evidence, test_merge_sha=test_merge,
                            ci={"id": check["id"], "url": check["html_url"], "head_sha": check["head_sha"], "evidence": ci_evidence})

    def merge(self):
        old = self.journal.last("qualified", "result")
        require(old, "Qualified candidate receipt is missing")
        checked_file(old["manifest"], self.root)
        verify_qualification(old["manifest"]["path"], self.plan["candidate_sha"], self.plan["candidate_tree"], self.plan["required_component_ids"])
        pr = self.find_integration_pr()
        require(pr is not None, "Integration PR is missing")
        if pr.get("merged"):
            self.journal.append("merge", "reconciled", merge_sha=self.merge_identity())
            return
        require(pr["head"]["sha"] == self.plan["candidate_sha"], "PR head changed after qualification")
        require(self.remote_ref(self.plan["base_ref"]) == self.plan["base_sha"], "Base changed after qualification")
        require(pr.get("merge_commit_sha") == old["test_merge_sha"], "Effective test merge changed after qualification")
        check = self.required_ci(self.plan["candidate_sha"])
        self.ci_evidence(check, {self.plan["candidate_sha"], old["test_merge_sha"]})
        statuses = self.backend.api(self.endpoint(f"/commits/{self.plan['candidate_sha']}/statuses?per_page=100"), paginate=True)
        latest = next((s for s in statuses if s["context"] == NATIVE_CONTEXT), None)
        require(latest and latest["state"] == "success"
                and latest["creator"]["id"] == self.plan["inventory"]["actor"]["id"]
                and latest["description"] == f"Receipt sha256 {old['manifest']['sha256'][:40]}",
                "Live native qualification status no longer matches the receipt")
        self.check_rules()
        self.journal.intent("merge", expected={"base": self.plan["base_sha"], "head": self.plan["candidate_sha"]}, desired="merge-commit")
        response = self.backend.api(self.endpoint(f"/pulls/{pr['number']}/merge"), "PUT",
                                    {"sha": self.plan["candidate_sha"], "merge_method": "merge"})
        require(response.get("merged") is True, "GitHub did not report a successful merge")
        merged = self.merge_identity()
        require(merged == response["sha"], "GitHub merge response and observed merge differ")
        self.journal.append("merge", "result", merge_sha=merged)

    def verify_merged(self):
        merged = self.merge_identity()
        require(self.remote_ref(self.plan["base_ref"]) == merged, "Default advanced before final verification")
        evidence = self.native_gate(merged)
        check = self.required_ci(merged)
        ci_evidence = self.ci_evidence(check, {merged})
        self.set_native_status(merged, evidence)
        self.journal.append("merged-qualified", "result", **evidence, ci={"id": check["id"], "url": check["html_url"], "evidence": ci_evidence})

    def merged_gate(self):
        merged = self.merge_identity()
        event = self.journal.last("merged-qualified", "result")
        require(event and event["source_sha"] == merged, "Final merge qualification is missing")
        checked_file(event["manifest"], self.root)
        verify_qualification(event["manifest"]["path"], merged, self.plan["candidate_tree"], self.plan["required_component_ids"])
        return merged

    def rename(self):
        merged = self.merged_gate()
        old_ref = self.plan["base_ref"]
        repository = self.backend.api(self.endpoint())
        heads = self.backend.remote_refs()
        self.journal.intent("rename", expected={old_ref: merged}, desired={"refs/heads/main": merged})
        if repository["default_branch"] == "main" and heads.get("refs/heads/main") == merged and old_ref not in heads:
            self.journal.append("rename", "reconciled", default_ref="refs/heads/main", source_sha=merged)
            return
        require("refs/heads/main" not in heads and heads.get(old_ref) == merged, "Rename collision or source drift")
        require(repository["default_branch"] == old_ref[11:], "Current default name changed")
        self.backend.api(self.endpoint(f"/branches/{quote(old_ref[11:], safe='')}/rename"), "POST", {"new_name": "main"})
        # One bounded observation window; a still-running rename remains resumable.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            repository, heads = self.backend.api(self.endpoint()), self.backend.remote_refs()
            if repository["default_branch"] == "main" and heads.get("refs/heads/main") == merged and old_ref not in heads:
                self.journal.append("rename", "result", default_ref="refs/heads/main", source_sha=merged)
                return
            time.sleep(2)
        raise GateError("GitHub rename is not yet confirmed; resume rename after it settles")

    def main_gate(self):
        merged = self.merged_gate()
        repo = self.backend.api(self.endpoint())
        require(repo["default_branch"] == "main" and self.remote_ref("refs/heads/main") == merged,
                "main is not the exact qualified default")
        return merged

    def resolve(self):
        merged = self.main_gate()
        original = {p["number"]: p for p in self.plan["inventory"]["prs"]}
        for item in self.plan["dispositions"]:
            number = int(item["number"])
            old = original[number]
            for entry in item["evidence"]:
                checked_file(entry, self.root)
            current = self.backend.pr(number)
            require(current["head"]["sha"] == old["head_sha"], f"PR #{number} changed")
            if current["merged"]:
                self.journal.append(f"resolve:{number}", "reconciled", outcome="merged", url=current["html_url"])
                continue
            disposition = item["disposition"]
            if disposition == "already_closed":
                require(current["state"] == "closed", f"PR #{number} was reopened")
                self.journal.append(f"resolve:{number}", "reconciled", outcome="already_closed")
                continue
            if disposition == "integrated":
                require(self.backend.ancestor(old["head_sha"], merged), "Task PR head is not reachable from main")
            archive = next((a for a in self.plan["archives"] if a["commit_sha"] == old["head_sha"]), None)
            require(archive, f"PR #{number} source head lacks an archive")
            marker = f"<!-- imagesorter-cutover:{self.plan['run_id']}:pr-{number} -->"
            comments = self.backend.api(self.endpoint(f"/issues/{number}/comments?per_page=100"), paginate=True)
            matches = [comment for comment in comments if marker in comment["body"]]
            require(len(matches) <= 1, "Duplicate disposition comments require inspection")
            wording = "Integrated through the unified candidate" if disposition == "integrated" else "Closed as superseded/ported, not directly merged"
            body = f"{marker}\n{wording}. Verified main: `{merged}`. Original head: `{old['head_sha']}`; archive: `{archive['ref']}`.\n\n{item['rationale']}\n\nEvidence manifest: `{digest(item['evidence'])}`."
            self.journal.intent(f"resolve:{number}", expected=old, desired={"state": "closed", "body": body})
            if matches:
                require(matches[0]["body"] == body and matches[0]["user"]["id"] == self.plan["inventory"]["actor"]["id"],
                        "Existing disposition comment differs or has another author")
            else:
                self.backend.api(self.endpoint(f"/issues/{number}/comments"), "POST", {"body": body})
            require(self.backend.pr(number)["head"]["sha"] == old["head_sha"], "PR advanced while recording its disposition")
            if current["state"] != "closed":
                self.backend.api(self.endpoint(f"/pulls/{number}"), "PATCH", {"state": "closed"})
            require(self.backend.pr(number)["state"] == "closed", "PR closure not confirmed")
            self.journal.append(f"resolve:{number}", "result", outcome=disposition, source_sha=old["head_sha"])

    def dependencies(self, name, rows=None):
        return [p for p in (rows if rows is not None else self.backend.prs()) if p["state"] == "open"
                and (p["base_ref"] == name[11:] or (p["head_ref"] == name[11:]
                    and p["head_repository_id"] == self.plan["inventory"]["repository_id"]))]

    def delete_branch(self, name, expected):
        ref(name)
        sha(expected)
        allowed = dict(self.plan["inventory"]["branches"])
        allowed[self.plan["integration_ref"]] = self.plan["candidate_sha"]
        require(allowed.get(name) == expected, "Deletion is outside the exact reviewed plan")
        current_default = self.backend.api(self.endpoint())["default_branch"]
        require(name not in {"refs/heads/main", f"refs/heads/{current_default}", self.plan["base_ref"]}, "Default branches are never deletion targets")
        self.archive_gate(expected)
        operation = f"delete:{name}"
        current = self.remote_ref(name)
        if current is None:
            require(self.journal.last(operation, "intent"), "Unrecorded branch disappearance requires review")
            self.restore_if_dependent(name, expected)
            self.journal.append(operation, "reconciled", observed=None, attribution="not-inferred")
            return
        require(not self.dependencies(name), f"Open PR depends on {name}")
        require(current == expected, f"Branch advanced before deletion: {name}")
        self.journal.intent(operation, expected=expected, desired=None)
        self.backend.git("-c", "push.followTags=false", "push", "--porcelain",
                         f"--force-with-lease={name}:{expected}", "origin", f":{name}")
        require(self.remote_ref(name) is None, "Deleted branch has been recreated; do not delete it again")
        self.restore_if_dependent(name, expected)
        self.journal.append(operation, "result", observed=None, deleted_sha=expected)

    def restore_if_dependent(self, name, expected):
        after = self.backend.prs()
        # Check all PRs, including ones GitHub may have closed when their base vanished.
        known = {p["number"] for p in self.plan["inventory"]["prs"]}
        new_dependency = [p for p in after if p["number"] not in known and p["base_ref"] == name[11:]]
        if self.dependencies(name, after) or new_dependency:
            self.journal.intent(f"restore:{name}", expected=None, desired=expected)
            self.backend.git("-c", "push.followTags=false", "push", "--porcelain",
                             f"--force-with-lease={name}:", "origin", f"{expected}:{name}")
            require(self.remote_ref(name) == expected, "Compensating restoration could not be verified")
            self.journal.append(f"restore:{name}", "result", source_sha=expected, reason="new-pr-dependency")
            raise GateError("A PR dependency raced deletion; archived branch restored, cleanup stopped")

    def prune(self):
        self.main_gate()
        for pr in self.backend.prs():
            require(pr["state"] == "closed", f"PR #{pr['number']} remains open")
        for name, oid in self.plan["inventory"]["branches"].items():
            if name not in {self.plan["base_ref"], self.plan["integration_ref"]}:
                self.check_rules()
                self.delete_branch(name, oid)
        pr = self.find_integration_pr()
        require(pr and pr["merged"], "Integration branch PR is not merged")
        self.delete_branch(self.plan["integration_ref"], self.plan["candidate_sha"])

    def finalize(self):
        merged = self.main_gate()
        require(self.backend.remote_refs() == {"refs/heads/main": merged}, "Unexpected branches remain")
        require(not any(p["state"] == "open" for p in self.backend.prs()), "Open PRs remain")
        self.archive_gate()
        # Preserve permanent main quality and archive protections; remove only this run's maintenance lock.
        quality = self.desired_rules()[1]
        quality["conditions"]["ref_name"]["include"] = ["refs/heads/main"]
        binding = self.journal.last("quality-app-binding", "result")
        require(binding, "CI source binding evidence missing")
        quality["rules"][-1]["parameters"]["required_status_checks"] = [binding["body"]["rules"][-1]["parameters"]["required_status_checks"][0]]
        rules = self.live_rules()
        current = next(r for r in rules if r["name"] == quality["name"])
        require(contains(current, self.desired_rules()[1]) or contains(current, quality), "Quality policy changed externally")
        self.journal.intent("final-quality", expected=self.rule_body(current), desired=quality)
        updated = self.backend.api(self.endpoint(f"/rulesets/{current['id']}"), "PUT", quality)
        require(contains(updated, quality), "Permanent main quality policy not verified")
        self.journal.append("final-quality", "result", id=current["id"], body=quality)
        before_auto_delete = self.plan["inventory"]["delete_branch_on_merge"]
        self.action("restore-auto-delete", False, before_auto_delete,
                    lambda: self.backend.api(self.endpoint(), "PATCH", {"delete_branch_on_merge": before_auto_delete}),
                    lambda: self.backend.api(self.endpoint()).get("delete_branch_on_merge"))
        maintenance = self.desired_rules()[0]
        current = next((r for r in rules if r["name"] == maintenance["name"]), None)
        self.journal.intent("unfreeze", expected=maintenance, desired=None)
        if current:
            require(contains(current, maintenance), "Maintenance policy changed externally")
            self.backend.api(self.endpoint(f"/rulesets/{current['id']}"), "DELETE")
        require(not any(r["name"] == maintenance["name"] for r in self.live_rules()), "Maintenance lock removal unconfirmed")
        final_heads = self.backend.remote_refs()
        require(final_heads == {"refs/heads/main": merged}, "Branches changed while releasing maintenance controls")
        self.journal.append("complete", "result", source_sha=merged, remote_branches=final_heads)

    def run(self, phase):
        # Every remotely mutating phase, including policies and archive tags,
        # requires complete local qualification before even reconciliation can
        # restore a remote branch. Inspect/plan/receipt/dry-run remain read-only.
        require(self.qualification is not None, "Supply --qualification with complete native evidence")
        require(self.distribution is not None, "Supply --distribution with preserved publication/source evidence")
        publication_source = self.publication_source(phase)
        self.native_gate(publication_source)
        self.publication_gate(publication_source)
        self.identity()
        for event in list(self.journal.events):
            if event["stage"] == "intent" and event["operation"].startswith("delete:"):
                name = event["operation"][7:]
                if self.remote_ref(name) is None:
                    self.restore_if_dependent(name, event["expected"])
        if phase != "prepare":
            if phase != "finalize" or not self.journal.last("final-quality", "intent"):
                self.check_rules()
            self.check_drift()
        getattr(self, phase.replace("-", "_"))()

    def publication_source(self, phase):
        if phase not in {"verify-merged", "rename", "resolve", "prune", "finalize"}:
            return self.plan["candidate_sha"]
        event = next((event for event in reversed(self.journal.events) if event["operation"] == "merge"
                      and event["stage"] in {"result", "reconciled"}), None)
        require(event is not None, "Resume merge to record its exact identity before post-merge qualification")
        return sha(event.get("merge_sha", ""))

    def publication_gate(self, source_sha=None):
        require(self.distribution is not None, "Supply --distribution with preserved publication/source evidence")
        source_sha = source_sha or self.plan["candidate_sha"]
        try:
            if __package__:
                from .publication_guard import verify_release
            else:
                from publication_guard import verify_release
            # Backend text strips trailing newlines. Read immutable Git bytes
            # directly so catalogue hashing cannot normalize its trust identity.
            catalog = self.backend.command(["git", "show", f'{source_sha}:src/imagesorter/resources/component_catalog.json'], binary=True)
            result = verify_release(catalog, self.distribution, self.qualification, source_sha, self.plan["candidate_tree"])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise GateError(f"Publication evidence failed: {exc}") from exc
        self.journal.append("publication-gate", "verified", **result)
        return result

    def receipt(self):
        return {
            "schema_version": SCHEMA, "run_id": self.plan["run_id"], "plan_sha256": digest(self.plan),
            "inventory_sha256": self.plan["inventory_sha256"],
            "complete": self.journal.last("complete", "result") is not None,
            "last_event_sha256": self.journal.events[-1]["event_sha256"] if self.journal.events else None,
            "events": self.journal.events,
        }


def contains(actual, expected):
    """Compare requested policy fields while tolerating server-added defaults."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            any(contains(item, wanted) for item in actual) for wanted in expected)
    return actual == expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "plan", "run", "resume", "receipt"))
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default="SyrisBruhh42/Image_Sorter")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--dispositions", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--distribution", type=Path, help="Exact artifact/source/terms publication evidence; required for every execute phase")
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--execute", action="store_true", help="Execute only the explicitly selected phase")
    args = parser.parse_args(argv)
    try:
        backend = Backend(args.checkout, args.repository)
        if args.command == "inspect":
            result = backend.snapshot()
        elif args.command == "plan":
            require(all((args.inventory, args.bundle, args.dispositions, args.run_id)), "plan requires inventory, bundle, dispositions, and run-id")
            result = make_plan(backend, load_json(args.inventory), args.bundle, load_json(args.dispositions), args.run_id)
        else:
            require(args.plan and args.evidence_root, "A plan and evidence-root are required")
            plan = load_json(args.plan)
            if args.command != "receipt":
                require(args.plan_sha256 == digest(plan), "Supply the exact reviewed --plan-sha256")
                require(args.phase, "Select one explicit --phase")
            with exclusive(args.evidence_root):
                controller = Controller(backend, plan, args.evidence_root, args.qualification, args.distribution)
                if args.command != "receipt" and args.execute:
                    controller.run(args.phase)
                result = controller.receipt()
                if args.command != "receipt" and not args.execute:
                    result["dry_run"] = True
                    result["selected_phase"] = args.phase
        if args.output:
            immutable_json(args.output, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (GateError, KeyError, TypeError, OSError, ValueError) as exc:
        print(f"Cutover stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
