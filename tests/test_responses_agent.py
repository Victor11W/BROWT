"""Responses agent transport, resource lifecycle and source isolation; no API calls."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/autonomy"))
try:
    SPEC = importlib.util.spec_from_file_location(
        "browt_responses_agent", ROOT / ".github/autonomy/responses_agent.py"
    )
    agent = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(agent)
finally:
    sys.path.pop(0)


def final_result(mode="probe", *, files=None):
    return {
        "outcome": mode,
        "summary": "API répond",
        "evidence": [],
        "files": files or [],
        "tickets": [],
    }


def completed(result):
    return {
        "id": "resp_test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "Working"}],
            },
            {
                "type": "message",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": json.dumps(result)}],
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


class FakeAPI:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or completed(final_result())
        self.fail_response = False

    def upload(self, path):
        self.calls.append(("UPLOAD", str(path), None))
        return {"id": "file-source"}

    def call(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        if path == "/containers":
            return {"id": "cntr_test"}
        if path == "/responses":
            if self.fail_response:
                raise agent.Stop("OpenAI rejected the request (HTTP 429; insufficient_quota).")
            return copy.deepcopy(self.response)
        if method == "DELETE":
            return {"deleted": True}
        if path.endswith("/cancel"):
            return {"status": "cancelled"}
        raise AssertionError((method, path, data))


def test_probe_uses_responses_without_files_container_or_codex(monkeypatch):
    monkeypatch.delenv("BROWT_API_MODEL", raising=False)
    api = FakeAPI()
    result = agent.execute(api, {"mode": "probe"}, root=ROOT)
    assert json.loads(result)["outcome"] == "probe"
    assert [(method, path) for method, path, _ in api.calls] == [("POST", "/responses")]
    request = api.calls[0][2]
    assert request["model"] == "gpt-6.2-sol"
    assert request["max_output_tokens"] == 1024
    assert request["text"]["format"]["strict"]
    assert "tools" not in request
    assert "codex" not in request["model"]


def test_work_has_offline_hosted_shell_and_cleans_only_its_own_resources(monkeypatch):
    data = final_result(
        "progress", files=[{"path": "src/BROWT/example.py", "content": "value = 42\n"}]
    )
    data["evidence"] = ["pytest : contrôles exécutés dans le conteneur"]
    api = FakeAPI(completed(data))

    def no_local_model_commands(*args, **kwargs):
        raise AssertionError("The model cannot execute commands on the API-key runner.")

    monkeypatch.setattr(agent, "command", no_local_model_commands)
    assert json.loads(agent.execute(api, {"mode": "work"}, root=ROOT))["outcome"] == "progress"
    container = next(data for method, path, data in api.calls if path == "/containers")
    assert container["file_ids"] == ["file-source"]
    assert container["network_policy"] == {"type": "disabled"}
    assert container["memory_limit"] == "1g"
    assert container["expires_after"]["minutes"] == 20
    request = next(data for method, path, data in api.calls if path == "/responses")
    assert request["tools"] == [
        {
            "type": "shell",
            "environment": {"type": "container_reference", "container_id": "cntr_test"},
        }
    ]
    assert request["max_tool_calls"] == 12
    assert ("DELETE", "/containers/cntr_test", None) in api.calls
    assert ("DELETE", "/files/file-source", None) in api.calls


def test_quota_failure_is_not_retried_and_resources_are_cleaned():
    api = FakeAPI()
    api.fail_response = True
    with pytest.raises(agent.Stop, match="insufficient_quota"):
        agent.execute(api, {"mode": "work"}, root=ROOT)
    assert sum(path == "/responses" for _, path, _ in api.calls) == 1
    assert ("DELETE", "/containers/cntr_test", None) in api.calls
    assert ("DELETE", "/files/file-source", None) in api.calls


def test_background_timeout_requests_cancellation():
    api = FakeAPI()
    with pytest.raises(agent.Stop, match="time limit"):
        agent.poll(
            api,
            {"id": "resp_test", "status": "in_progress"},
            now=lambda: 1,
            sleep=lambda _: None,
            limit=0,
        )
    assert api.calls == [("POST", "/responses/resp_test/cancel", {})]


def test_background_poll_waits_for_completed_response():
    class PollAPI(FakeAPI):
        def call(self, method, path, data=None):
            self.calls.append((method, path, data))
            return completed(final_result())

    api = PollAPI()
    response = agent.poll(
        api, {"id": "resp_test", "status": "queued"}, now=lambda: 0, sleep=lambda _: None
    )
    assert response["status"] == "completed"
    assert api.calls == [("GET", "/responses/resp_test", None)]


@pytest.mark.parametrize(
    "response",
    [
        {"id": None, "status": "completed"},
        {"id": "https://other.invalid", "status": "completed"},
        {"id": "resp_test", "status": "incomplete"},
        {"id": "resp_test", "status": "failed"},
    ],
)
def test_unfinished_or_invalid_responses_fail_closed(response):
    with pytest.raises(agent.Stop):
        agent.poll(FakeAPI(), response, now=lambda: 0, sleep=lambda _: None)


def test_refusals_and_missing_final_results_are_errors():
    with pytest.raises(agent.Stop, match="no final"):
        agent.response_text({"output": []})
    with pytest.raises(agent.Stop, match="declined"):
        agent.response_text({"output": [{"type": "message", "content": [{"type": "refusal"}]}]})


def test_model_data_is_validated_before_leaving_the_api_job():
    data = final_result(
        "progress", files=[{"path": ".github/workflows/ci.yml", "content": "changed"}]
    )
    data["evidence"] = ["fake"]
    with pytest.raises(agent.Stop):
        agent.execute(FakeAPI(completed(data)), {"mode": "work"}, root=ROOT)


def test_plan_reads_project_context_without_sandbox_or_upload():
    api = FakeAPI(completed(final_result("planned")))
    agent.execute(api, {"mode": "plan", "backlog": []}, root=ROOT)
    assert len(api.calls) == 1
    request = api.calls[0][2]
    assert "docs/roadmap.qmd" in request["input"][0]["content"]
    assert "tools" not in request


@pytest.mark.parametrize("key", ["", "bad\nkey", "bad key", "bad\x16key"])
def test_control_characters_are_rejected_before_any_request(key):
    with pytest.raises(agent.Stop):
        agent.OpenAI(key)


def test_api_http_error_hides_key_url_and_raw_error_message():
    api = agent.OpenAI("local-secret-value")

    class Rejected:
        def open(self, request, timeout):
            body = io.BytesIO(
                b'{"error":{"code":"insufficient_quota","message":"local-secret-value https://private.invalid"}}'
            )
            raise urllib.error.HTTPError(
                "https://private.invalid/local-secret-value", 429, "local-secret-value", {}, body
            )

    api.opener = Rejected()
    with pytest.raises(agent.Stop) as error:
        api.call("POST", "/responses", {"input": "probe"})
    assert str(error.value) == "OpenAI rejected the request (HTTP 429; insufficient_quota)."


@pytest.mark.parametrize(
    "path",
    ["https://other.invalid", "/../responses", "/responses/a/../../files", "/responses/a?key=bad"],
)
def test_api_origin_and_paths_are_fixed(path):
    with pytest.raises(agent.Stop, match="Invalid OpenAI API path"):
        agent.OpenAI("local-secret-value").call("GET", path)


def test_source_upload_has_expiration_and_never_embeds_the_key(tmp_path):
    source = tmp_path / "source.zip"
    source.write_bytes(b"source-fixture")
    api = agent.OpenAI("local-secret-value")
    seen = []

    class Accepted:
        def open(self, request, timeout):
            seen.append(request)
            return io.BytesIO(b'{"id":"file-fixture"}')

    api.opener = Accepted()
    assert api.upload(source)["id"] == "file-fixture"
    request = seen[0]
    assert request.full_url == "https://api.openai.com/v1/files"
    assert request.get_header("Authorization") == "Bearer local-secret-value"
    assert b"local-secret-value" not in request.data
    assert b"expires_after[seconds]" in request.data and b"3600" in request.data
    assert b"user_data" in request.data


def test_non_work_modes_do_not_create_dependency_bundles(tmp_path):
    assert agent.bundle({"mode": "probe"}, root=tmp_path) is None
    assert agent.bundle({"mode": "plan"}, root=tmp_path) is None
    assert not (tmp_path / agent.BUNDLE_FILE).exists()


def test_bundle_uses_real_git_archive_and_excludes_untracked_files(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("git is required for the source archival integration test")
    config = '[project]\ndependencies=["numpy>=1.24"]\n[project.optional-dependencies]\nresearch=["matplotlib>=3.7"]\ndev=["pytest>=7.4"]\n'
    (tmp_path / "pyproject.toml").write_text(config)
    (tmp_path / "README.md").write_text("public source")
    (tmp_path / ".env").write_text("private-untracked-value")
    for args in (
        ["init", "-q"],
        ["add", "README.md", "pyproject.toml"],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=tests@localhost",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    real_run = subprocess.run

    def offline_prepare(args, **kwargs):
        if args[0] == "git":
            return real_run(args, **kwargs)
        assert args[:4] == [sys.executable, "-m", "pip", "download"]
        target = Path(args[args.index("--dest") + 1])
        (target / "fixture-1.0-py3-none-any.whl").write_bytes(b"wheel-fixture")
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(agent.subprocess, "run", offline_prepare)
    target = agent.bundle({"mode": "work"}, root=tmp_path)
    with zipfile.ZipFile(target) as archive:
        source = archive.read("source.zip")
        assert "manifest.json" in archive.namelist()
        assert "task.json" in archive.namelist()
        with zipfile.ZipFile(io.BytesIO(source)) as original:
            assert sorted(original.namelist()) == ["README.md", "pyproject.toml"]
            assert b"private-untracked-value" not in b"".join(
                original.read(name) for name in original.namelist()
            )
    with pytest.raises(agent.Stop, match="fresh runner"):
        agent.bundle({"mode": "work"}, root=tmp_path)


def test_interrupted_background_wait_requests_cancellation():
    api = FakeAPI()

    def interrupted(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        agent.poll(
            api, {"id": "resp_test", "status": "in_progress"}, now=lambda: 0, sleep=interrupted
        )
    assert api.calls == [("POST", "/responses/resp_test/cancel", {})]


def test_rerunning_only_the_model_job_stops_before_key_or_task_access(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["responses_agent.py", "run"])
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    def no_key(*args, **kwargs):
        raise AssertionError("A rerun must stop before API-key access.")

    monkeypatch.setattr(agent, "OpenAI", no_key)
    with pytest.raises(agent.Stop, match="cannot be rerun"):
        agent.main()
