"""Trusted hosted controller. The model only returns data across a job boundary.

Run from the repository root. No model-generated code is executed in this process.
State lives in an owner/bot-authored issue, serialized by workflow concurrency.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

STATE_MARKER = "<!-- browt-worker-state:v1 -->"
BOT = "github-actions[bot]"
READY, BLOCKED, BUSY = "browt:ready", "browt:blocked", "browt:in-progress"
LABELS = {READY: "0e8a16", BLOCKED: "b60205", BUSY: "1d76db", "browt:state": "ededed"}
MAX_SESSIONS = 2
MAX_NOTICES = 2
MAX_RESULT_BYTES = 60_000
FOUNDATION_SHA = "809ed66765e89e7f2ca8f5554709625a7262ae5b"


class Stop(RuntimeError):
    """A redacted, actionable failure safe to print."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, repo, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise Stop("Invalid repository name.")
        self.repo = repo
        self.owner = repo.split("/")[0]
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def call(self, method, endpoint, data=None):
        if not isinstance(endpoint, str):
            raise Stop("Invalid API path.")
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            decoded_path = urllib.parse.unquote(parsed.path)
        except ValueError:
            raise Stop("Invalid API path.") from None
        # A compare route contains BASE...HEAD. Reject traversal segments, not dots
        # inside a legitimate route component. Never allow an origin override.
        if endpoint and (
            not endpoint.startswith("/")
            or endpoint.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or decoded_path.startswith("//")
            or "#" in endpoint
            or "\\" in decoded_path
            or "%" in decoded_path
            or any(part in {".", ".."} for part in decoded_path.split("/"))
            or any(ord(char) <= 32 or ord(char) == 127 for char in endpoint + decoded_path)
        ):
            raise Stop("Invalid API path.")
        request = urllib.request.Request(
            "https://api.github.com/repos/" + self.repo + endpoint,
            method=method,
            data=None if data is None else json.dumps(data).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "BROWT-worker",
            },
        )
        try:
            with self.opener.open(request, timeout=25) as response:
                body = response.read(4_000_001)
            if len(body) > 4_000_000:
                raise Stop("GitHub response is too large.")
            return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            raise Stop("GitHub request rejected (HTTP " + str(error.code) + ").") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise Stop("GitHub is unreachable or returned invalid JSON.") from None

    def pages(self, endpoint):
        result = []
        for page in range(1, 11):
            joiner = "&" if "?" in endpoint else "?"
            items = self.call("GET", endpoint + joiner + f"per_page=100&page={page}")
            if not isinstance(items, list):
                raise Stop("Unexpected GitHub collection.")
            result.extend(items)
            if len(items) < 100:
                return result
        raise Stop("GitHub collection exceeds the controller's page limit.")


def today():
    return datetime.now(timezone.utc).date().isoformat()


def fresh_state():
    return {
        "version": 1,
        "day": today(),
        "sessions": 0,
        "notices": 0,
        "probe_ok": False,
        "verified": False,
        "paused": "",
        "active": None,
        "reservation": None,
        "planner_fingerprint": "",
        "notice": None,
        "last_notice": "",
    }


def state_body(state):
    return STATE_MARKER + "\n\n" + json.dumps(state, ensure_ascii=False, indent=2) + "\n"


def parse_state(body):
    try:
        state = json.loads(body.removeprefix(STATE_MARKER).strip())
        expected = fresh_state()
        if set(state) != set(expected) or state["version"] != 1:
            raise ValueError
        for field in ("sessions", "notices"):
            if type(state[field]) is not int or not 0 <= state[field] <= 100:
                raise ValueError
        for field in ("probe_ok", "verified"):
            if type(state[field]) is not bool:
                raise ValueError
        for field in ("day", "paused", "planner_fingerprint", "last_notice"):
            if not isinstance(state[field], str):
                raise ValueError
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", state["day"]):
            raise ValueError
        for field in ("active", "reservation", "notice"):
            if state[field] is not None and not isinstance(state[field], dict):
                raise ValueError
        return state
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Stop("Worker state is invalid; inspect the state issue before continuing.") from None


def trusted(issue, owner):
    return issue.get("user", {}).get("login", "").lower() in {owner.lower(), BOT}


def labels(issue):
    return {item["name"] for item in issue.get("labels", [])}


def managed(issue, owner):
    body = issue.get("body") or ""
    return (
        trusted(issue, owner)
        and ("<!-- browt-seed:" in body or "<!-- browt-task:" in body)
        and STATE_MARKER not in body
        and not issue.get("pull_request")
    )


def set_status(api, issue, status):
    new = (labels(issue) - {READY, BLOCKED, BUSY}) | {status}
    if new != labels(issue):
        api.call("PUT", f"/issues/{issue['number']}/labels", {"labels": sorted(new)})
        issue["labels"] = [{"name": label} for label in sorted(new)]


def safe_path(name):
    if not isinstance(name, str) or len(name) > 160 or not name.isascii():
        raise Stop("Invalid result path.")
    parts = name.split("/")
    if any(not part or part in {".", ".."} or part.startswith(".") for part in parts):
        raise Stop("Hidden or traversing paths are forbidden.")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
        raise Stop("Invalid result path characters.")
    if any(part.lower().startswith("agents") for part in parts):
        raise Stop("Instruction files require a separate reviewed change.")
    if name in {"configs/dataset-lock.json", "configs/dataset-source.yaml"}:
        raise Stop("Frozen dataset metadata cannot be edited by the worker.")
    roots = {"src", "tests", "docs", "configs"}
    root_files = {"README.md", "CONTRIBUTING.md", "pyproject.toml"}
    extensions = {
        ".py",
        ".qmd",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".csv",
        ".tsv",
        ".svg",
        ".txt",
        ".toml",
        ".css",
    }
    if name not in root_files and (len(parts) < 2 or parts[0] not in roots):
        raise Stop("Result path is outside the worker's allowlist.")
    if PurePosixPath(name).suffix not in extensions:
        raise Stop("Only supported text files can be published.")
    if any(part in {"_book", "_site", "node_modules", "__pycache__"} for part in parts):
        raise Stop("Generated directories cannot be published.")
    return name


def text_ok(value, limit):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise Stop("Invalid or oversized result text.")
    patterns = (
        r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
        r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}",
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
        r"\b\d{6,}:[A-Za-z0-9_-]{25,}",
    )
    if any(re.search(pattern, value) for pattern in patterns):
        raise Stop("A possible credential was rejected before publication.")
    return value


def validate_result(raw, mode):
    if not raw or len(raw.encode()) > MAX_RESULT_BYTES:
        raise Stop("Model result is empty or exceeds 60 KB.")
    try:
        result = json.loads(raw)
        if set(result) != {"outcome", "summary", "evidence", "files", "tickets"}:
            raise ValueError
        allowed = (
            {"probe"}
            if mode == "probe"
            else {"planned", "no_change"}
            if mode == "plan"
            else {"progress", "completed", "blocked", "no_change"}
        )
        if result["outcome"] not in allowed:
            raise ValueError
        text_ok(result["summary"], 240)
        if not result["summary"].strip() or len(result["summary"].splitlines()) != 1:
            raise ValueError
        if not isinstance(result["evidence"], list) or len(result["evidence"]) > 12:
            raise ValueError
        for item in result["evidence"]:
            text_ok(item, 500)
        if not isinstance(result["files"], list) or len(result["files"]) > 12:
            raise ValueError
        seen = set()
        for item in result["files"]:
            if set(item) != {"path", "content"}:
                raise ValueError
            name = safe_path(item["path"])
            if name.lower() in seen:
                raise ValueError
            seen.add(name.lower())
            if item["content"] is not None:
                text_ok(item["content"], 50_000)
        if not isinstance(result["tickets"], list) or len(result["tickets"]) > 3:
            raise ValueError
        for item in result["tickets"]:
            if set(item) != {"key", "title", "body", "priority", "ready", "depends_on"}:
                raise ValueError
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", item["key"]):
                raise ValueError
            text_ok(item["title"], 140)
            text_ok(item["body"], 5000)
            if not item["title"].strip() or not item["body"].strip():
                raise ValueError
            if type(item["priority"]) is not int or item["priority"] not in (0, 1, 2):
                raise ValueError
            if type(item["ready"]) is not bool or not isinstance(item["depends_on"], list):
                raise ValueError
            if len(item["depends_on"]) > 10 or any(
                type(n) is not int or n < 1 for n in item["depends_on"]
            ):
                raise ValueError
        if mode == "work" and result["files"] and not result["evidence"]:
            raise ValueError
        if mode != "work" and result["files"]:
            raise ValueError
        if mode == "probe" and (result["tickets"] or result["evidence"]):
            raise ValueError
        if result["outcome"] == "no_change" and (result["files"] or result["tickets"]):
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Stop("Model result does not match the publishing contract.") from None


class Controller:
    def __init__(self, api, event, env):
        self.api, self.event, self.env = api, event, env
        self.repo = api.call("GET", "")
        self.default = self.repo["default_branch"]
        self.issues = api.pages("/issues?state=all")
        matches = [
            i
            for i in self.issues
            if STATE_MARKER in (i.get("body") or "") and trusted(i, api.owner)
        ]
        if len(matches) > 1:
            raise Stop("Duplicate worker state issues; resolve before continuing.")
        self.state_issue = matches[0] if matches else None
        self.state = parse_state(matches[0]["body"]) if matches else fresh_state()
        if self.state["day"] != today():
            self.state.update(day=today(), sessions=0, notices=0)

    def save(self):
        body = state_body(self.state)
        if self.state_issue:
            self.api.call(
                "PATCH", f"/issues/{self.state_issue['number']}", {"body": body, "state": "open"}
            )
        else:
            self.state_issue = self.api.call(
                "POST",
                "/issues",
                {
                    "title": "BROWT · état du worker hébergé",
                    "body": body,
                    "labels": ["browt:state"],
                },
            )

    def notice(self, key, text):
        if key != self.state["last_notice"]:
            self.state["notice"] = {"key": key, "text": text_ok(text, 350)}

    def pause(self, reason):
        self.state["paused"] = reason
        self.notice("pause:" + reason, "BROWT · pause\n" + reason)
        self.save()

    def main_sha(self):
        return self.api.call("GET", "/branches/" + self.default)["commit"]["sha"]

    def setup_labels(self):
        existing = {label["name"] for label in self.api.pages("/labels")}
        for name, color in LABELS.items():
            if name not in existing:
                self.api.call("POST", "/labels", {"name": name, "color": color})

    def seed_statuses(self):
        for issue in self.issues:
            if not managed(issue, self.api.owner) or issue["state"] != "open":
                continue
            if labels(issue) & {READY, BLOCKED, BUSY}:
                continue
            body = issue.get("body") or ""
            set_status(
                self.api, issue, READY if "browt-seed:grouped-dataset-split" in body else BLOCKED
            )

    def complete_setup_tickets(self):
        """Close setup objectives only when their external evidence exists."""
        seeds = {
            "publish-research-foundation": False,
            "hosted-autonomy": self.state["verified"],
        }
        open_seeds = [i for i in self.issues if managed(i, self.api.owner) and i["state"] == "open"]
        if any(
            "browt-seed:publish-research-foundation" in (i.get("body") or "") for i in open_seeds
        ):
            sha = self.main_sha()
            comparison = self.api.call("GET", "/compare/" + FOUNDATION_SHA + "..." + sha)
            runs = self.api.call("GET", "/actions/workflows/ci.yml/runs?event=push&per_page=100")[
                "workflow_runs"
            ]
            seeds["publish-research-foundation"] = comparison["status"] in {
                "ahead",
                "identical",
            } and any(
                r["head_sha"] == sha
                and r["head_branch"] == self.default
                and r["conclusion"] == "success"
                for r in runs
            )
        for issue in open_seeds:
            for key, complete in seeds.items():
                if complete and ("browt-seed:" + key) in (issue.get("body") or ""):
                    self.api.call("PATCH", f"/issues/{issue['number']}", {"state": "closed"})
                    issue["state"] = "closed"

    def candidates(self):
        return sorted(
            [
                i
                for i in self.issues
                if managed(i, self.api.owner) and i["state"] == "open" and READY in labels(i)
            ],
            key=lambda i: (
                int(re.search(r"\[P([012])\]", i["title"])[1])
                if re.search(r"\[P([012])\]", i["title"])
                else 2,
                i["number"],
            ),
        )

    def dependencies(self):
        by_number = {i["number"]: i for i in self.issues if not i.get("pull_request")}
        for issue in by_number.values():
            if (
                not managed(issue, self.api.owner)
                or issue["state"] != "open"
                or BLOCKED not in labels(issue)
            ):
                continue
            match = re.search(r"<!-- browt-deps:([0-9,]+) -->", issue.get("body") or "")
            if match and all(
                by_number.get(int(n), {}).get("state") == "closed" for n in match[1].split(",")
            ):
                set_status(self.api, issue, READY)

    def create_tickets(self, tickets):
        count = 0
        known = {i["number"] for i in self.issues if not i.get("pull_request")}
        for task in tickets:
            if not set(task["depends_on"]) <= known:
                raise Stop("A proposed ticket depends on an unknown issue.")
            marker = "<!-- browt-task:" + task["key"] + " -->"
            title = f"[P{task['priority']}] " + task["title"]
            if any(
                marker in (i.get("body") or "") or i["title"].casefold() == title.casefold()
                for i in self.issues
                if not i.get("pull_request")
            ):
                continue
            body = task["body"] + "\n\n" + marker
            deps = task["depends_on"]
            if deps:
                body += "\nDépendances : " + ", ".join("#" + str(n) for n in deps)
                if task["ready"]:
                    body += "\n<!-- browt-deps:" + ",".join(map(str, deps)) + " -->"
            unmet = any(
                next(i for i in self.issues if i["number"] == n)["state"] != "closed" for n in deps
            )
            status = READY if task["ready"] and not unmet else BLOCKED
            created = self.api.call(
                "POST", "/issues", {"title": title, "body": body, "labels": [status]}
            )
            self.issues.append(created)
            count += 1
        return count

    def task_fingerprint(self):
        data = [
            (i["number"], i["title"], i.get("body"), i["state"], sorted(labels(i)))
            for i in self.issues
            if managed(i, self.api.owner)
        ]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def initial_credit_probe_allowed(self):
        """One tiny probe after an exact-SHA main CI, authorized by funding policy.

        An explicit false flag remains a kill switch. Work and scheduled sessions
        continue to require the ordinary activation flags and acceptance cycle.
        """
        if self.env.get("BROWT_API_BUDGET_CONFIRMED") == "false":
            return False
        if (
            self.state["probe_ok"]
            or self.state["paused"]
            or self.state["reservation"]
            or self.state["active"]
        ):
            return False
        run = self.event.get("workflow_run") or {}
        if (
            run.get("conclusion") != "success"
            or run.get("head_branch") != self.default
            or run.get("head_repository", {}).get("full_name") != self.api.repo
            or run.get("path") != ".github/workflows/ci.yml"
        ):
            return False
        try:
            policy = json.loads(
                Path(".github/autonomy/budget-policy.json").read_text(encoding="utf-8")
            )
            funding = policy["funding"]
            approved = (
                policy.get("repository") == self.api.repo
                and funding.get("initial_eligible_probe_after_ci") is True
                and funding.get("status") == "declared_by_user"
                and funding.get("source") == "eligible_promotional_api_credits"
                and funding.get("api_paid_plan") == "not_started_confirmed_by_user"
            )
        except (OSError, ValueError, TypeError, KeyError):
            return False
        return approved and run.get("head_sha") == self.main_sha()

    def prepare(self):
        self.setup_labels()
        self.seed_statuses()
        self.dependencies()
        self.reconcile()
        self.complete_setup_tickets()
        if (
            self.state["paused"] == "Invalid API path."
            and self.state["reservation"] is None
            and self.state["active"] is None
            and self.state["sessions"] == 0
        ):
            # Recover only the known pre-model initialization defect, after its
            # setup checks have succeeded. Other pauses retain manual resume.
            self.state["paused"] = ""
            self.notice(
                "controller-initialized",
                "BROWT · contrôleur initialisé\nLe premier essai attend le financement et le contrôle du budget.",
            )
        mode = self.event.get("inputs", {}).get("mode", "work")
        event_name = self.env["GITHUB_EVENT_NAME"]
        automatic_probe = event_name == "workflow_run" and self.initial_credit_probe_allowed()
        if event_name == "workflow_run":
            if automatic_probe:
                mode = "probe"
            else:
                self.save()
                return None
        if mode == "resume":
            if event_name != "workflow_dispatch":
                raise Stop("Resume is manual only.")
            self.state.update(paused="", reservation=None)
            self.save()
            return None
        if mode == "inspect":
            self.save()
            return None
        if self.env.get("BROWT_API_BUDGET_CONFIRMED") != "true" and not automatic_probe:
            print("No model call: BROWT_API_BUDGET_CONFIRMED is not true.")
            self.save()
            return None
        scheduled = event_name == "schedule"
        if scheduled and (
            self.env.get("BROWT_AUTONOMY_ENABLED") != "true" or not self.state["verified"]
        ):
            print("No model call: scheduled autonomy has not passed activation gates.")
            self.save()
            return None
        if self.state["paused"] or self.state["sessions"] >= MAX_SESSIONS:
            self.save()
            return None
        if int(self.env.get("GITHUB_RUN_ATTEMPT", "1")) != 1:
            print("A model session must use a new dispatch, not a workflow rerun.")
            self.save()
            return None
        reservation = self.state["reservation"]
        if reservation:
            self.pause(
                "Une session précédente est restée incomplète ; vérifier son journal puis utiliser resume."
            )
            return None
        if mode != "probe" and not self.state["probe_ok"]:
            print("No model call: a successful manual probe is required first.")
            self.save()
            return None
        base = self.event["workflow_run"]["head_sha"] if automatic_probe else self.main_sha()
        issue, repair, source = None, False, base
        active = self.state["active"]
        if active:
            if not active.get("failed"):
                self.save()
                return None
            if active["failures"] >= 3:
                self.pause("Trois échecs de CI pour la même PR ; une revue est nécessaire.")
                return None
            issue = next((i for i in self.issues if i["number"] == active["issue"]), None)
            repair, source = True, active["head"]
        elif mode == "work":
            choices = self.candidates()
            if choices:
                issue = choices[0]
            else:
                mode = "plan"
                if self.task_fingerprint() == self.state["planner_fingerprint"]:
                    self.save()
                    return None
        if repair and (mode != "work" or not issue):
            self.save()
            return None
        reservation = {
            "run": self.env["GITHUB_RUN_ID"],
            "mode": mode,
            "issue": issue["number"] if issue else None,
            "base": base,
            "source": source,
            "repair": repair,
            "manual": not scheduled,
        }
        self.state["sessions"] += 1
        self.state["reservation"] = reservation
        if issue:
            set_status(self.api, issue, BUSY)
        self.save()  # Reserve before a billable action, including interrupted actions.
        context = {
            "mode": mode,
            "reservation": reservation,
            "task": {
                "number": issue["number"],
                "title": issue["title"],
                "body": (issue.get("body") or "")[:12_000],
            }
            if issue
            else None,
            "backlog": [
                {
                    "number": i["number"],
                    "title": i["title"],
                    "state": i["state"],
                    "labels": sorted(labels(i)),
                }
                for i in self.issues
                if managed(i, self.api.owner)
            ][:150],
            "previous_pr": active if repair else None,
        }
        return context

    def publish(self, raw, agent_status):
        reservation = self.state["reservation"]
        if not reservation or reservation["run"] != self.env["GITHUB_RUN_ID"]:
            raise Stop("No matching reserved model session.")
        if agent_status != "success":
            self.state["reservation"] = None
            self.pause(
                "La session API a échoué ; vérifier les crédits, le modèle et le journal avant resume."
            )
            return
        result = validate_result(raw, reservation["mode"])
        if self.main_sha() != reservation["base"]:
            raise Stop("Main changed during the session; review and resume from the new revision.")
        count = self.create_tickets(result["tickets"])
        if reservation["mode"] == "probe":
            self.state["probe_ok"] = True
            self.notice(
                "probe-ok",
                "BROWT · API répond\nLe test API est réussi ; la planification reste à valider.",
            )
        elif reservation["mode"] == "plan":
            self.state["planner_fingerprint"] = self.task_fingerprint()
            if count:
                self.notice(
                    "plan:" + self.state["planner_fingerprint"],
                    f"BROWT · {count} ticket(s) préparé(s)\nLes prochaines tâches sont dans GitHub.",
                )
        elif result["files"]:
            self.publish_pr(reservation, result)
        else:
            issue = reservation["issue"]
            if result["outcome"] == "completed":
                raise Stop("A ticket cannot be completed without a checked code change.")
            self.api.call("POST", f"/issues/{issue}/comments", {"body": self.result_body(result)})
            task = next(i for i in self.issues if i["number"] == issue)
            # no_change is blocked until new evidence arrives; avoid repeated paid no-ops.
            set_status(self.api, task, BLOCKED)
            if reservation["repair"]:
                self.state["paused"] = (
                    "La réparation ne propose aucun changement ; une revue est nécessaire."
                )
            self.notice(
                "blocked:" + str(issue), f"BROWT · ticket #{issue} en attente\n" + result["summary"]
            )
        self.state["reservation"] = None
        self.save()

    @staticmethod
    def result_body(result):
        return (
            result["summary"]
            + "\n\nPreuves déclarées par le worker (la CI reste autoritaire) :\n"
            + "\n".join("- " + item for item in result["evidence"])
        )

    def publish_pr(self, reservation, result):
        source = reservation["source"]
        commit = self.api.call("GET", "/git/commits/" + source)
        tree = self.api.call("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
        if tree.get("truncated"):
            raise Stop("Repository tree is truncated.")
        entries = {entry["path"]: entry for entry in tree["tree"]}
        changes = []
        for item in result["files"]:
            name = item["path"]
            if any(
                entries.get("/".join(name.split("/")[:n]), {}).get("mode") in {"120000", "160000"}
                for n in range(1, len(name.split("/")) + 1)
            ):
                raise Stop("Symlinks and submodules cannot be changed.")
            old = entries.get(name)
            if old and old["type"] != "blob":
                raise Stop("Only ordinary text files may be changed.")
            if item["content"] is None:
                if not old:
                    raise Stop("Cannot delete a missing file.")
                sha = None
            else:
                blob = self.api.call(
                    "POST", "/git/blobs", {"content": item["content"], "encoding": "utf-8"}
                )
                sha = blob["sha"]
                if old and sha == old["sha"]:
                    continue
            changes.append(
                {"path": name, "mode": old["mode"] if old else "100644", "type": "blob", "sha": sha}
            )
        if not changes:
            raise Stop("The proposed files contain no actual change.")
        new_tree = self.api.call(
            "POST", "/git/trees", {"base_tree": commit["tree"]["sha"], "tree": changes}
        )
        new_commit = self.api.call(
            "POST",
            "/git/commits",
            {"message": result["summary"], "tree": new_tree["sha"], "parents": [source]},
        )
        sha = new_commit["sha"]
        branch = f"codex/agent-{reservation['issue']}-{reservation['run']}"
        active = self.state["active"]
        if reservation["repair"]:
            branch = active["branch"]
            current = self.api.call("GET", "/git/ref/heads/" + branch)["object"]["sha"]
            if current != source:
                raise Stop("Worker branch changed; refusing to overwrite it.")
            self.api.call("PATCH", "/git/refs/heads/" + branch, {"sha": sha, "force": False})
            pr_number = active["pr"]
            failures = active["failures"]
            self.api.call(
                "POST", f"/issues/{pr_number}/comments", {"body": self.result_body(result)}
            )
        else:
            self.api.call("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": sha})
            pr = self.api.call(
                "POST",
                "/pulls",
                {
                    "head": branch,
                    "base": self.default,
                    "draft": False,
                    "title": result["summary"],
                    "body": f"Ticket #{reservation['issue']}\n\n"
                    + self.result_body(result)
                    + "\n\n<!-- browt-worker-pr:v1 -->",
                },
            )
            pr_number, failures = pr["number"], 0
        self.state["active"] = {
            "pr": pr_number,
            "branch": branch,
            "head": sha,
            "base": reservation["base"],
            "issue": reservation["issue"],
            "outcome": result["outcome"],
            "summary": result["summary"],
            "manual": reservation["manual"],
            "failures": failures,
            "failed": False,
            "ci_url": "",
            "failed_steps": [],
            "dispatch_pending": True,
        }
        self.save()  # Recoverable even if dispatch fails or the runner stops here.
        self.dispatch_checks()

    def dispatch_checks(self):
        active = self.state["active"]
        self.api.call("POST", "/actions/workflows/ci.yml/dispatches", {"ref": active["branch"]})
        active["dispatch_pending"] = False
        self.save()

    def reconcile(self):
        active = self.state["active"]
        if not active:
            return
        pr = self.api.call("GET", f"/pulls/{active['pr']}")
        if pr.get("merged"):
            if not self.valid_pr(pr, active):
                self.pause("La PR intégrée ne correspond plus à la révision suivie.")
                return
            self.finish_active(pr)
            return
        if pr["state"] == "closed":
            task = next(i for i in self.issues if i["number"] == active["issue"])
            set_status(self.api, task, BLOCKED)
            self.notice(
                "closed:" + str(active["pr"]),
                f"BROWT · PR #{active['pr']} fermée\nLe ticket reste ouvert pour réexamen.",
            )
            self.state["active"] = None
            return
        if not self.valid_pr(pr, active):
            self.pause("La PR suivie a changé hors du worker ; une revue est nécessaire.")
            return
        if active.get("dispatch_pending"):
            self.dispatch_checks()
            return
        runs = self.api.call(
            "GET", "/actions/workflows/ci.yml/runs?event=workflow_dispatch&per_page=100"
        )["workflow_runs"]
        matches = [
            r
            for r in runs
            if r.get("event") == "workflow_dispatch"
            and r["head_sha"] == active["head"]
            and r["head_branch"] == active["branch"]
            and r.get("path", ".github/workflows/ci.yml") == ".github/workflows/ci.yml"
        ]
        if not matches:
            return
        run = max(matches, key=lambda r: r["id"])
        active["ci_url"] = run["html_url"]
        if run["status"] != "completed":
            return
        if run["conclusion"] != "success":
            if active.get("failed_run") != run["id"]:
                active["failures"] += 1
                active.update(failed=True, failed_run=run["id"])
                jobs = self.api.call("GET", f"/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
                active["failed_steps"] = [
                    j["name"] + ": " + s["name"]
                    for j in jobs
                    for s in j.get("steps", [])
                    if s.get("conclusion") == "failure"
                ][:12]
                self.notice(
                    "ci:" + str(run["id"]),
                    f"BROWT · correction nécessaire sur la PR #{active['pr']}\n" + run["html_url"],
                )
            return
        if self.state["paused"] or self.env.get("BROWT_API_BUDGET_CONFIRMED") != "true":
            return
        if not active["manual"] and self.env.get("BROWT_AUTONOMY_ENABLED") != "true":
            return
        if self.main_sha() != active["base"]:
            self.pause("Main a changé avant fusion ; la PR doit être réévaluée.")
            return
        files = self.api.pages(f"/pulls/{active['pr']}/files")
        if not files or len(files) > 36:
            raise Stop("Unexpected PR file count.")
        for file in files:
            safe_path(file["filename"])
            if file.get("previous_filename"):
                safe_path(file["previous_filename"])
        active["ci_verified"] = True
        self.save()
        merged = self.api.call(
            "PUT", f"/pulls/{active['pr']}/merge", {"sha": active["head"], "merge_method": "merge"}
        )
        if not merged.get("merged"):
            raise Stop("GitHub did not confirm the merge; branch rules may require review.")
        self.finish_active({"merge_commit_sha": merged["sha"]})

    def valid_pr(self, pr, active):
        return (
            pr["head"]["sha"] == active["head"]
            and pr["head"]["ref"] == active["branch"]
            and pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"]
            and pr["base"]["ref"] == self.default
            and pr["base"]["repo"]["full_name"] == self.api.repo
            and pr.get("user", {}).get("login") == BOT
            and "<!-- browt-worker-pr:v1 -->" in (pr.get("body") or "")
            and not pr.get("draft")
        )

    def finish_active(self, pr):
        active = self.state["active"]
        issue = next(i for i in self.issues if i["number"] == active["issue"])
        body = f"PR #{active['pr']} intégrée. CI : {active['ci_url']}\nRévision : {pr.get('merge_commit_sha', 'à consulter dans la PR')}"
        # A stable marker avoids duplicate comments after an interrupted finalization.
        marker = f"<!-- browt-merged:{active['pr']} -->"
        comments = self.api.pages(f"/issues/{issue['number']}/comments")
        if not any(marker in (c.get("body") or "") for c in comments):
            self.api.call(
                "POST", f"/issues/{issue['number']}/comments", {"body": body + "\n" + marker}
            )
        if active["outcome"] == "completed" and active.get("ci_verified"):
            self.api.call("PATCH", f"/issues/{issue['number']}", {"state": "closed"})
        else:
            set_status(
                self.api,
                issue,
                BLOCKED if active["outcome"] in {"blocked", "no_change", "completed"} else READY,
            )
        if active.get("ci_verified"):
            if not active.get("docs_dispatched"):
                self.api.call(
                    "POST", "/actions/workflows/publish.yml/dispatches", {"ref": self.default}
                )
                active["docs_dispatched"] = True
                self.save()
            self.state["verified"] = True
        self.notice(
            "merged:" + str(active["pr"]),
            f"BROWT · PR #{active['pr']} intégrée\n" + active["summary"],
        )
        self.state["active"] = None
        self.save()


def output(name, value):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(name + "=" + value + "\n")


def notify(controller, env):
    state = controller.state
    notice = state["notice"]
    if not notice or notice["key"] == state["last_notice"] or state["notices"] >= MAX_NOTICES:
        return
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = env.get("TELEGRAM_CHAT_ID", "").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?[0-9]+", chat):
        raise Stop("Configure Telegram secrets before sending updates.")
    state.update(notices=state["notices"] + 1, last_notice=notice["key"], notice=None)
    controller.save()  # At most once: an interrupted delivery is not automatically repeated.
    text = notice["text"]
    if len(text.splitlines()) > 2:
        raise Stop("A Telegram update must contain at most two lines.")
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=json.dumps(
            {"chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True}}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise Stop("Telegram rejected the update (HTTP " + str(error.code) + ").") from None
    except (OSError, ValueError, http.client.HTTPException):
        raise Stop("Telegram is unreachable or returned invalid JSON.") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise Stop("Telegram did not confirm delivery.")
    print("Telegram confirmed delivery.")


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    controller = Controller(api, event, os.environ)
    command = sys.argv[1]
    try:
        if command == "prepare":
            task = controller.prepare()
            output("run_model", "true" if task else "false")
            if task:
                output("source", task["reservation"]["source"])
                output("task", json.dumps(task, ensure_ascii=True, separators=(",", ":")))
        elif command == "publish":
            controller.publish(
                os.environ.get("MODEL_RESULT", ""), os.environ.get("AGENT_STATUS", "")
            )
        elif command == "notify":
            if controller.state_issue:
                notify(controller, os.environ)
        else:
            raise Stop("Unknown controller command.")
    except Stop as error:
        if command != "notify":
            controller.pause(str(error))
        raise


if __name__ == "__main__":
    try:
        main()
    except Stop as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
