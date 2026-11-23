"""Hosted control flow with a fake GitHub API; no keys or network calls."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hosted_worker", ROOT / ".github/autonomy/worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
MAIN_SHA, HEAD_SHA = "a" * 40, "b" * 40


def issue(number, key, *, state="open", status=None, author=worker.BOT):
    return {
        "number": number,
        "title": "[P0] " + key,
        "body": "<!-- browt-seed:" + key + " -->",
        "state": state,
        "labels": [{"name": status}] if status else [],
        "user": {"login": author},
    }


class FakeGitHub:
    owner = "Victor11W"
    repo = "Victor11W/BROWT"

    def __init__(self):
        self.calls = []
        self.issues = [issue(3, "grouped-dataset-split", status=worker.READY)]
        self.prs = {}
        self.comments = {}
        self.runs = []
        self.jobs = []
        self.sha = MAIN_SHA
        self.branch_sha = HEAD_SHA
        self.tree = {
            "tree": [
                {"path": "src/BROWT/example.py", "mode": "100644", "type": "blob", "sha": "old"}
            ]
        }
        self.files = [{"filename": "src/BROWT/example.py"}]
        self.fail_merge = False

    def pages(self, endpoint):
        if endpoint == "/issues?state=all":
            return copy.deepcopy(self.issues)
        if endpoint == "/labels":
            return [{"name": name} for name in worker.LABELS]
        if endpoint.endswith("/files"):
            return self.files
        if endpoint.endswith("/comments"):
            return self.comments.get(int(endpoint.split("/")[2]), [])
        raise AssertionError(endpoint)

    def call(self, method, endpoint, data=None):
        self.calls.append((method, endpoint, copy.deepcopy(data)))
        if method == "GET":
            if endpoint == "":
                return {"default_branch": "main"}
            if endpoint.startswith("/branches/"):
                return {"commit": {"sha": self.sha}}
            if endpoint.startswith("/git/commits/"):
                return {"tree": {"sha": "source-tree"}}
            if endpoint.startswith("/git/trees/"):
                return self.tree
            if endpoint.startswith("/git/ref/heads/"):
                return {"object": {"sha": self.branch_sha}}
            if endpoint.startswith("/actions/workflows/ci.yml/runs"):
                return {"workflow_runs": self.runs}
            if endpoint.startswith("/actions/runs/") and "/jobs" in endpoint:
                return {"jobs": self.jobs}
            if endpoint.startswith("/compare/"):
                return {"status": "ahead"}
            if endpoint.startswith("/pulls/"):
                return copy.deepcopy(self.prs[int(endpoint.split("/")[2])])
        if method == "POST":
            if endpoint == "/issues":
                created = {
                    "number": len(self.issues) + 10,
                    "state": "open",
                    "user": {"login": worker.BOT},
                    **data,
                }
                created["labels"] = [{"name": name} for name in data.get("labels", [])]
                self.issues.append(created)
                return copy.deepcopy(created)
            if endpoint.endswith("/comments"):
                self.comments.setdefault(int(endpoint.split("/")[2]), []).append(data)
                return {}
            if endpoint == "/git/blobs":
                return {"sha": "new-blob"}
            if endpoint == "/git/trees":
                return {"sha": "new-tree"}
            if endpoint == "/git/commits":
                return {"sha": HEAD_SHA}
            if endpoint == "/pulls":
                created = {
                    "number": 20,
                    "state": "open",
                    "draft": False,
                    "merged": False,
                    "body": data["body"],
                    "user": {"login": worker.BOT},
                    "head": {
                        "sha": HEAD_SHA,
                        "ref": data["head"],
                        "repo": {"full_name": self.repo},
                    },
                    "base": {"ref": "main", "repo": {"full_name": self.repo}},
                }
                self.prs[20] = created
                return copy.deepcopy(created)
            if endpoint in {
                "/git/refs",
                "/actions/workflows/ci.yml/dispatches",
                "/actions/workflows/publish.yml/dispatches",
                "/labels",
            }:
                return {}
        if method in {"PATCH", "PUT"}:
            if endpoint.startswith("/issues/"):
                number = int(endpoint.split("/")[2])
                found = next(i for i in self.issues if i["number"] == number)
                if endpoint.endswith("/labels"):
                    found["labels"] = [{"name": name} for name in data["labels"]]
                else:
                    found.update(data)
                return copy.deepcopy(found)
            if endpoint.endswith("/merge"):
                if self.fail_merge:
                    return {"merged": False}
                number = int(endpoint.split("/")[2])
                self.prs[number].update(merged=True, state="closed", merge_commit_sha="c" * 40)
                return {"merged": True, "sha": "c" * 40}
            if endpoint.startswith("/git/refs/heads/"):
                return {}
        raise AssertionError((method, endpoint, data))


def make_controller(
    api=None, *, mode="work", event="workflow_dispatch", budget="true", enabled="false"
):
    api = api or FakeGitHub()
    env = {
        "GITHUB_EVENT_NAME": event,
        "GITHUB_RUN_ID": "100",
        "GITHUB_RUN_ATTEMPT": "1",
        "BROWT_API_BUDGET_CONFIRMED": budget,
        "BROWT_AUTONOMY_ENABLED": enabled,
    }
    controller = worker.Controller(api, {"inputs": {"mode": mode}}, env)
    controller.state["probe_ok"] = True
    return controller


def result(outcome="progress", *, files=True, tickets=None):
    return json.dumps(
        {
            "outcome": outcome,
            "summary": "Ajoute une étape de recherche vérifiable",
            "evidence": ["pytest : 1 test passé"],
            "files": [{"path": "src/BROWT/example.py", "content": "value = 42\n"}] if files else [],
            "tickets": tickets or [],
        }
    )


def published(outcome="progress"):
    controller = make_controller()
    controller.prepare()
    controller.publish(result(outcome), "success")
    return controller


def successful_ci(controller, *, conclusion="success", sha=HEAD_SHA, event="workflow_dispatch"):
    active = controller.state["active"]
    controller.api.runs = [
        {
            "id": 200,
            "head_sha": sha,
            "head_branch": active["branch"],
            "event": event,
            "path": ".github/workflows/ci.yml",
            "html_url": "https://github.com/Victor11W/BROWT/actions/runs/200",
            "status": "completed",
            "conclusion": conclusion,
        }
    ]


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "src/../README.md",
        "src/.env",
        ".github/workflows/ci.yml",
        "src/x\\y.py",
        "C:/temp/x.py",
        "src/AGENTS.md",
        "docs/AGENTS.override.md",
        "configs/dataset-lock.json",
        "configs/dataset-source.yaml",
        "data/raw/x.txt",
        "docs/_book/index.html",
        "docs/x.png",
        "src//x.py",
        "/src/x.py",
        "src/é.py",
        "docs/x.py\n",
        "src/a:stream.py",
    ],
)
def test_rejects_paths_outside_text_allowlist(name):
    with pytest.raises(worker.Stop):
        worker.safe_path(name)


@pytest.mark.parametrize(
    "name",
    [
        "src/BROWT/split.py",
        "tests/test_split.py",
        "docs/figures/grouped.svg",
        "configs/grouped.yaml",
        "pyproject.toml",
    ],
)
def test_accepts_expected_research_files(name):
    assert worker.safe_path(name) == name


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(extra="untrusted"),
        lambda r: r.update(outcome="unknown"),
        lambda r: r.update(summary="two\nlines"),
        lambda r: r.update(files=r["files"] * 2),
        lambda r: r["files"][0].update(path=".github/workflows/ci.yml"),
        lambda r: r["files"][0].update(content="x" * 60_001),
        lambda r: r["files"][0].update(content="sk-proj-" + "Q" * 40),
    ],
)
def test_rejects_invalid_model_data_before_writes(mutation):
    data = json.loads(result())
    mutation(data)
    with pytest.raises(worker.Stop):
        worker.validate_result(json.dumps(data), "work")


def test_probe_must_be_minimal_and_cannot_edit_files():
    minimal = json.dumps(
        {"outcome": "probe", "summary": "Codex répond", "files": [], "evidence": [], "tickets": []}
    )
    assert worker.validate_result(minimal, "probe")["outcome"] == "probe"
    with pytest.raises(worker.Stop):
        worker.validate_result(result("probe"), "probe")


def test_daily_reservation_precedes_model_and_blocks_duplicate():
    controller = make_controller()
    task = controller.prepare()
    assert task["reservation"]["source"] == MAIN_SHA
    assert controller.state["sessions"] == 1
    saved = worker.parse_state(controller.api.issues[-1]["body"])
    assert saved["reservation"]["run"] == "100"
    assert controller.prepare() is None
    assert controller.state["paused"]
    assert controller.state["sessions"] == 1


@pytest.mark.parametrize(
    "budget,enabled,verified,expected",
    [
        ("", "true", True, False),
        ("true", "", True, False),
        ("true", "true", False, False),
        ("true", "true", True, True),
    ],
)
def test_scheduled_sessions_require_all_activation_gates(budget, enabled, verified, expected):
    controller = make_controller(event="schedule", budget=budget, enabled=enabled)
    controller.state["verified"] = verified
    assert bool(controller.prepare()) is expected


def test_unfunded_probe_and_inspect_make_no_model_reservation():
    for mode in ("inspect", "probe", "work"):
        controller = make_controller(mode=mode, budget="false")
        assert controller.prepare() is None
        assert controller.state["sessions"] == 0


@pytest.mark.parametrize("field,value", [("sessions", 2), ("paused", "quota")])
def test_limit_or_pause_prevents_model_call(field, value):
    controller = make_controller()
    controller.state[field] = value
    assert controller.prepare() is None


def test_rerun_cannot_repeat_a_billable_session():
    controller = make_controller()
    controller.env["GITHUB_RUN_ATTEMPT"] = "2"
    assert controller.prepare() is None


def test_work_requires_real_probe_but_probe_can_run_without_it():
    controller = make_controller()
    controller.state["probe_ok"] = False
    assert controller.prepare() is None
    controller.event["inputs"]["mode"] = "probe"
    assert controller.prepare()["mode"] == "probe"


def test_public_issue_cannot_trigger_model_work():
    api = FakeGitHub()
    api.issues = [issue(3, "grouped-dataset-split", status=worker.READY, author="stranger")]
    controller = make_controller(api)
    task = controller.prepare()
    assert task["mode"] == "plan"
    assert task["task"] is None


def test_failure_keeps_ticket_open_and_pauses_instead_of_retrying():
    controller = make_controller()
    controller.prepare()
    controller.publish("", "failure")
    assert controller.state["paused"]
    assert controller.state["reservation"] is None
    assert controller.api.issues[0]["state"] == "open"
    assert not controller.api.prs


def test_complete_ticket_waits_for_matching_dispatched_ci_then_closes():
    controller = published("completed")
    assert controller.api.issues[0]["state"] == "open"
    assert any(endpoint.endswith("/ci.yml/dispatches") for _, endpoint, _ in controller.api.calls)
    assert controller.state["active"]["pr"] == 20
    successful_ci(controller, sha="wrong-sha")
    controller.reconcile()
    assert not controller.api.prs[20]["merged"]
    successful_ci(controller)
    controller.reconcile()
    assert controller.api.prs[20]["merged"]
    assert controller.api.issues[0]["state"] == "closed"
    assert controller.state["verified"]
    assert controller.state["active"] is None
    merge = next(
        data
        for method, endpoint, data in controller.api.calls
        if method == "PUT" and endpoint.endswith("/merge")
    )
    assert merge["sha"] == HEAD_SHA


def test_progress_merge_requeues_parent_instead_of_closing_it():
    controller = published()
    successful_ci(controller)
    controller.reconcile()
    assert controller.api.issues[0]["state"] == "open"
    assert worker.READY in worker.labels(controller.api.issues[0])


def test_failure_can_be_repaired_but_is_counted_once_per_ci_run():
    controller = published()
    successful_ci(controller, conclusion="failure")
    controller.api.jobs = [{"name": "CPU", "steps": [{"name": "pytest", "conclusion": "failure"}]}]
    controller.reconcile()
    controller.reconcile()
    active = controller.state["active"]
    assert active["failures"] == 1
    assert active["failed_steps"] == ["CPU: pytest"]
    controller.env["GITHUB_RUN_ID"] = "101"
    task = controller.prepare()
    assert task["reservation"]["repair"]
    assert task["reservation"]["source"] == HEAD_SHA
    controller.publish(result(), "success")
    assert len(controller.api.prs) == 1
    assert any(
        method == "PATCH" and endpoint.startswith("/git/refs/") and data["force"] is False
        for method, endpoint, data in controller.api.calls
    )


def test_three_failed_ci_runs_stop_repairs():
    controller = published()
    controller.state["active"].update(failed=True, failures=3)
    assert controller.prepare() is None
    assert controller.state["paused"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.api.prs[20]["head"].update(sha="changed"),
        lambda c: c.api.prs[20]["head"]["repo"].update(full_name="stranger/fork"),
        lambda c: c.api.prs[20].update(user={"login": "stranger"}),
        lambda c: c.api.prs[20].update(draft=True),
        lambda c: setattr(c.api, "sha", "concurrent-main"),
    ],
)
def test_changed_pr_or_main_is_never_merged(mutation):
    controller = published()
    successful_ci(controller)
    mutation(controller)
    controller.reconcile()
    assert not controller.api.prs[20]["merged"]
    assert controller.state["paused"]


def test_branch_rules_are_not_bypassed():
    controller = published()
    controller.api.fail_merge = True
    successful_ci(controller)
    with pytest.raises(worker.Stop, match="branch rules"):
        controller.reconcile()
    assert controller.api.issues[0]["state"] == "open"


def test_symlink_ancestor_cannot_be_used_to_publish_elsewhere():
    controller = make_controller()
    controller.prepare()
    controller.api.tree["tree"].append(
        {"path": "src/BROWT", "mode": "120000", "type": "blob", "sha": "link"}
    )
    with pytest.raises(worker.Stop, match="Symlinks"):
        controller.publish(result(), "success")
    assert not controller.api.prs


def test_external_merge_does_not_claim_worker_end_to_end_verification():
    controller = published()
    controller.api.prs[20].update(merged=True, state="closed")
    controller.reconcile()
    assert not controller.state["verified"]


def test_planner_deduplicates_closed_tickets_and_does_not_repeat_on_unchanged_backlog():
    controller = make_controller()
    controller.api.issues[0]["state"] = "closed"
    controller.issues[0]["state"] = "closed"
    task = controller.prepare()
    assert task["mode"] == "plan"
    planned = {
        "key": "next-step",
        "title": "Étudier les groupes",
        "body": "Critères : tests et protocole",
        "priority": 0,
        "ready": False,
        "depends_on": [3],
    }
    data = result("planned", files=False, tickets=[planned])
    controller.publish(data, "success")
    assert controller.create_tickets([planned]) == 0
    controller.state["sessions"] = 0
    assert controller.prepare() is None


def test_state_round_trip_and_fail_closed_on_corruption():
    state = worker.fresh_state()
    assert worker.parse_state(worker.state_body(state)) == state
    state["sessions"] = -1
    with pytest.raises(worker.Stop):
        worker.parse_state(worker.state_body(state))


def test_duplicate_trusted_state_is_an_error_but_spoofed_state_is_ignored():
    api = FakeGitHub()
    spoof = issue(8, "spoof", author="stranger")
    spoof["body"] = worker.state_body(worker.fresh_state())
    api.issues.append(spoof)
    assert worker.Controller(api, {}, {}).state_issue is None
    spoof["user"]["login"] = worker.BOT
    api.issues.append(copy.deepcopy(spoof))
    with pytest.raises(worker.Stop, match="Duplicate"):
        worker.Controller(api, {}, {})


def test_authentication_is_never_forwarded_through_a_redirect():
    assert (
        worker.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid")
        is None
    )


def test_http_error_never_exposes_authorization_or_url():
    api = worker.GitHub("Victor11W/BROWT", "private-value")

    class Rejected:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                "https://host/private-value", 403, "private-value", {}, None
            )

    api.opener = Rejected()
    with pytest.raises(worker.Stop) as error:
        api.call("GET", "")
    assert "403" in str(error.value)
    assert "private-value" not in str(error.value)


def test_notifications_are_short_deduplicated_and_reserved_before_send(monkeypatch):
    controller = make_controller()
    controller.setup_labels()
    controller.save()
    controller.notice("one", "BROWT · avancée\nPR intégrée")
    requests = []

    class Delivered:
        def open(self, request, timeout):
            requests.append(request)
            assert controller.state["notices"] == 1
            assert controller.state["last_notice"] == "one"
            return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(worker.urllib.request, "build_opener", lambda *args: Delivered())
    env = {"TELEGRAM_BOT_TOKEN": "123456:local-test-token", "TELEGRAM_CHAT_ID": "42"}
    worker.notify(controller, env)
    worker.notify(controller, env)
    assert len(requests) == 1
    assert len(json.loads(requests[0].data)["text"].splitlines()) == 2
    controller.state["notices"] = 2
    controller.notice("two", "BROWT · suite")
    worker.notify(controller, env)
    assert len(requests) == 1


def test_workflow_separates_secrets_and_keeps_codex_last():
    workflow = yaml.load(
        (ROOT / ".github/workflows/autonomous-worker.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    jobs = workflow["jobs"]
    assert "schedule" in workflow["on"]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    agent = jobs["agent"]
    assert agent["permissions"] == {"contents": "read"}
    assert agent["steps"][-1]["uses"] == "openai/codex-action@v1"
    assert agent["steps"][-1]["with"]["permission-profile"] == ":workspace"
    assert agent["steps"][-1]["with"]["safety-strategy"] == "drop-sudo"
    assert "sandbox" not in agent["steps"][-1]["with"]
    assert "secrets.TELEGRAM" not in json.dumps(agent)
    assert "secrets.OPENAI" not in json.dumps({k: v for k, v in jobs.items() if k != "agent"})
    for name in ("prepare", "publish", "notify"):
        assert "github.sha" in jobs[name]["steps"][0]["with"]["ref"]
        assert jobs[name]["steps"][0]["with"]["persist-credentials"] == "false"
        assert not re.search(
            r"\$\{\{.*(?:MODEL_RESULT|task).*\}\}",
            "\n".join(step.get("run", "") for step in jobs[name]["steps"]),
        )


def test_counters_persist_across_fresh_runners_and_roll_over_at_utc_day_change():
    controller = make_controller()
    controller.prepare()
    restored = make_controller(controller.api)
    assert restored.state["sessions"] == 1
    assert restored.state["reservation"]["issue"] == 3
    restored.state["day"] = "2000-01-01"
    restored.state["notices"] = 2
    restored.save()
    next_day = make_controller(controller.api)
    assert next_day.state["sessions"] == 0
    assert next_day.state["notices"] == 0
    assert next_day.state["reservation"] is not None


def test_manual_resume_does_not_repeat_incomplete_model_work():
    controller = make_controller()
    controller.prepare()
    controller.state["paused"] = "interrupted"
    controller.event["inputs"]["mode"] = "resume"
    assert controller.prepare() is None
    assert controller.state["paused"] == ""
    assert controller.state["reservation"] is None
    assert controller.state["sessions"] == 1


def test_foundation_ticket_closes_only_with_ancestor_and_successful_main_ci():
    api = FakeGitHub()
    api.issues.append(issue(2, "publish-research-foundation"))
    controller = make_controller(api, mode="inspect", budget="false")
    controller.prepare()
    assert api.issues[1]["state"] == "open"
    api.runs = [{"head_sha": MAIN_SHA, "head_branch": "main", "conclusion": "success"}]
    controller.prepare()
    assert api.issues[1]["state"] == "closed"


def test_funding_switch_off_stops_automatic_merge():
    controller = published()
    successful_ci(controller)
    controller.env["BROWT_API_BUDGET_CONFIRMED"] = "false"
    controller.reconcile()
    assert not controller.api.prs[20]["merged"]


def test_disabled_schedule_does_not_merge_a_scheduled_pr():
    controller = published()
    successful_ci(controller)
    controller.state["active"]["manual"] = False
    controller.reconcile()
    assert not controller.api.prs[20]["merged"]


def test_unrelated_ci_event_cannot_authorize_merge():
    controller = published()
    successful_ci(controller, event="pull_request")
    controller.reconcile()
    assert not controller.api.prs[20]["merged"]


def test_retargeted_base_cannot_be_merged():
    controller = published()
    successful_ci(controller)
    controller.api.prs[20]["base"]["ref"] = "other"
    controller.reconcile()
    assert not controller.api.prs[20]["merged"]
    assert controller.state["paused"]


def test_dispatched_ci_is_recoverable_from_persisted_pending_state():
    controller = published()
    controller.state["active"]["dispatch_pending"] = True
    controller.save()
    restored = make_controller(controller.api)
    restored.reconcile()
    assert not restored.state["active"]["dispatch_pending"]
    assert (
        sum(endpoint.endswith("/ci.yml/dispatches") for _, endpoint, _ in controller.api.calls) == 2
    )


def test_manually_merged_unchecked_change_does_not_close_the_research_ticket():
    controller = published("completed")
    controller.api.prs[20].update(merged=True, state="closed")
    controller.reconcile()
    assert controller.api.issues[0]["state"] == "open"
    assert worker.BLOCKED in worker.labels(controller.api.issues[0])
    assert not controller.state["verified"]


def test_repair_cannot_overwrite_someone_elses_push():
    controller = published()
    successful_ci(controller, conclusion="failure")
    controller.reconcile()
    controller.env["GITHUB_RUN_ID"] = "101"
    controller.prepare()
    controller.api.branch_sha = "other-head"
    with pytest.raises(worker.Stop, match="refusing to overwrite"):
        controller.publish(result(), "success")
    assert not any(
        method == "PATCH" and "/git/refs/" in endpoint
        for method, endpoint, _ in controller.api.calls
    )


def test_telegram_failure_is_redacted_and_not_repeated(monkeypatch):
    controller = make_controller()
    controller.save()
    controller.notice("failure", "BROWT · pause")

    class Rejected:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "private-value", {}, None)

    monkeypatch.setattr(worker.urllib.request, "build_opener", lambda *args: Rejected())
    env = {"TELEGRAM_BOT_TOKEN": "123456:private-value", "TELEGRAM_CHAT_ID": "42"}
    with pytest.raises(worker.Stop) as error:
        worker.notify(controller, env)
    assert str(error.value) == "Telegram rejected the update (HTTP 401)."
    assert controller.state["last_notice"] == "failure"
    worker.notify(controller, env)


def test_worker_merge_explicitly_publishes_the_book_without_duplicate_dispatch():
    controller = published()
    successful_ci(controller)
    controller.reconcile()
    controller.reconcile()
    dispatches = [
        data
        for method, endpoint, data in controller.api.calls
        if method == "POST" and endpoint == "/actions/workflows/publish.yml/dispatches"
    ]
    assert dispatches == [{"ref": "main"}]


def test_code_change_requires_declared_verification_evidence():
    data = json.loads(result())
    data["evidence"] = []
    with pytest.raises(worker.Stop):
        worker.validate_result(json.dumps(data), "work")


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "/compare/" + MAIN_SHA + "..." + HEAD_SHA,
        "/git/ref/heads/codex/agent-3-100",
        "/actions/workflows/ci.yml/runs?event=workflow_dispatch&per_page=100",
    ],
)
def test_real_client_accepts_legitimate_repository_routes(endpoint):
    api = worker.GitHub("Victor11W/BROWT", "local-test-token")
    requests = []

    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(b"{}")

    api.opener = Transport()
    assert api.call("GET", endpoint) == {}
    assert requests[0].full_url == "https://api.github.com/repos/Victor11W/BROWT" + endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://elsewhere.invalid",
        "//elsewhere.invalid/path",
        "/../issues",
        "/a/../../issues",
        "/%2e%2e/issues",
        "/%252e%252e/issues",
        "/a/%2e/issues",
        "/a%5c..%5cissues",
        "/issues\n",
        "/issues%0a",
        "/issues#fragment",
        "//[",
        "//",
        "/issues?x=\x00",
    ],
)
def test_real_client_rejects_traversal_and_origin_overrides_before_http(endpoint):
    api = worker.GitHub("Victor11W/BROWT", "local-test-token")

    class NoNetwork:
        def open(self, request, timeout):
            raise AssertionError("Rejected paths must never reach HTTP.")

    api.opener = NoNetwork()
    with pytest.raises(worker.Stop, match="Invalid API path"):
        api.call("GET", endpoint)


def test_first_hosted_initialization_uses_real_client_through_fake_http():
    backend = FakeGitHub()
    backend.issues.append(issue(2, "publish-research-foundation"))
    paused = worker.fresh_state()
    paused.update(paused="Invalid API path.", notices=1, last_notice="pause:Invalid API path.")
    state_issue = issue(8, "state")
    state_issue["body"] = worker.state_body(paused)
    backend.issues.append(state_issue)
    backend.runs = [{"head_sha": MAIN_SHA, "head_branch": "main", "conclusion": "success"}]
    api = worker.GitHub(backend.repo, "local-test-token")
    seen = []

    class HttpTransport:
        def open(self, request, timeout):
            url = worker.urllib.parse.urlsplit(request.full_url)
            assert url.scheme == "https" and url.netloc == "api.github.com"
            endpoint = url.path.removeprefix("/repos/" + backend.repo)
            query = worker.urllib.parse.parse_qs(url.query)
            seen.append((request.method, endpoint, query))
            if endpoint == "/issues" and request.method == "GET":
                payload = backend.pages("/issues?state=all")
            elif endpoint == "/labels" and request.method == "GET":
                payload = backend.pages("/labels")
            elif endpoint == "/actions/workflows/ci.yml/runs":
                payload = {"workflow_runs": backend.runs}
            else:
                data = json.loads(request.data) if request.data else None
                payload = backend.call(request.method, endpoint, data)
            return io.BytesIO(json.dumps(payload).encode())

    api.opener = HttpTransport()
    controller = worker.Controller(
        api,
        {},
        {
            "GITHUB_EVENT_NAME": "workflow_run",
            "GITHUB_RUN_ID": "300",
            "BROWT_API_BUDGET_CONFIRMED": "false",
            "BROWT_AUTONOMY_ENABLED": "false",
        },
    )
    assert controller.prepare() is None
    assert controller.state["sessions"] == 0
    assert controller.state["reservation"] is None
    assert controller.state["paused"] == ""
    assert not controller.state["probe_ok"] and not controller.state["verified"]
    assert backend.issues[1]["state"] == "closed"
    assert any("..." in endpoint for _, endpoint, _ in seen)
    persisted = worker.parse_state(backend.issues[2]["body"])
    assert persisted["paused"] == "" and persisted["sessions"] == 0
    assert persisted["notice"]["key"] == "controller-initialized"


@pytest.mark.parametrize(
    "pause,reservation,sessions",
    [
        ("quota", None, 0),
        ("Invalid API path.", {"run": "100"}, 0),
        ("Invalid API path.", None, 1),
    ],
)
def test_initialization_recovery_does_not_clear_other_or_billable_pauses(
    pause, reservation, sessions
):
    controller = make_controller(mode="inspect", budget="false")
    controller.state.update(paused=pause, reservation=reservation, sessions=sessions)
    assert controller.prepare() is None
    assert controller.state["paused"] == pause
    assert controller.state["reservation"] == reservation
