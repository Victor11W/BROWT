from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/hosted-setup.yml"
KEY = "sk-" + "not-a-real-key-" * 3
BOT_TOKEN = "123456789:" + "A" * 35
CHAT_ID = "1234567890"


@pytest.fixture
def workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def python_script(workflow, job):
    return next(step["run"] for step in workflow["jobs"][job]["steps"] if "run" in step)


def execute(script, env, handler):
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(urllib.request, "urlopen", side_effect=handler),
    ):
        exec(compile(script, "hosted-setup-inline", "exec"), {"__name__": "__main__"})


def test_bootstrap_only_uses_trusted_push_or_manual_events(workflow):
    assert set(workflow["on"]) == {"push", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["codex/research-foundation"]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"


def test_service_secrets_are_isolated_from_github_write_jobs(workflow):
    expected_jobs = {
        "OPENAI_API_KEY": "openai_auth",
        "TELEGRAM_BOT_TOKEN": "telegram",
        "TELEGRAM_CHAT_ID": "telegram",
    }
    found = set()
    for job_name, job in workflow["jobs"].items():
        for step in job["steps"]:
            for name in step.get("env", {}):
                if name in expected_jobs:
                    found.add(name)
                    assert job_name == expected_jobs[name]
                    assert job["permissions"] == {}
    assert found == set(expected_jobs)
    assert workflow["jobs"]["tickets"]["permissions"]["issues"] == "write"
    assert workflow["jobs"]["pull_request"]["permissions"]["pull-requests"] == "write"


def test_telegram_push_notification_requires_new_tickets_but_allows_manual_retry(workflow):
    job = workflow["jobs"]["telegram"]
    assert job["needs"] == "tickets"
    condition = job["if"]
    for clause in (
        "!cancelled()",
        "inputs.test_telegram",
        "needs.tickets.result == 'success'",
        "needs.tickets.outputs.created_count != '0'",
    ):
        assert clause in condition
    assert (
        "${{ steps.seed.outputs.result }}"
        == workflow["jobs"]["tickets"]["outputs"]["created_count"]
    )


def test_openai_probe_only_lists_models_without_printing_key_or_response(workflow, capsys):
    requests = []

    def listing(request, timeout):
        requests.append(request)
        assert timeout == 20
        assert request.get_header("Authorization") == "Bearer " + KEY
        return io.BytesIO(json.dumps({"data": [{"id": KEY}]}).encode())

    execute(python_script(workflow, "openai_auth"), {"OPENAI_API_KEY": KEY}, listing)
    assert len(requests) == 1
    assert requests[0].full_url == "https://api.openai.com/v1/models"
    assert requests[0].get_method() == "GET"
    assert requests[0].data is None
    output = capsys.readouterr()
    assert KEY not in output.out + output.err
    assert "No generation was requested" in output.out
    assert "does not validate Codex inference" in output.out


@pytest.mark.parametrize("status", [401, 403, 429])
def test_openai_errors_do_not_expose_key(workflow, capsys, status):
    def denied(request, timeout):
        raise urllib.error.HTTPError(request.full_url, status, KEY, {}, None)

    with pytest.raises(SystemExit) as error:
        execute(python_script(workflow, "openai_auth"), {"OPENAI_API_KEY": KEY}, denied)
    assert str(status) in str(error.value)
    output = capsys.readouterr()
    assert KEY not in str(error.value) + output.out + output.err


@pytest.mark.parametrize("key", ["", "sk-test\nInjected", "\x16"])
def test_invalid_key_is_rejected_before_network(workflow, key):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid key reached the network")

    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        execute(python_script(workflow, "openai_auth"), {"OPENAI_API_KEY": key}, unexpected)


def test_telegram_confirmation_uses_one_short_message_without_exposing_token(workflow, capsys):
    requests = []

    def sent(request, timeout):
        requests.append(request)
        assert timeout == 20
        return io.BytesIO(b'{"ok":true}')

    execute(
        python_script(workflow, "telegram"),
        {"TELEGRAM_BOT_TOKEN": BOT_TOKEN, "TELEGRAM_CHAT_ID": CHAT_ID},
        sent,
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    payload = json.loads(request.data)
    assert payload["chat_id"] == CHAT_ID
    assert len(payload["text"]) < 200
    assert payload["text"].count("\n") == 1
    assert "reste à configurer" in payload["text"]
    output = capsys.readouterr()
    assert BOT_TOKEN not in output.out + output.err


@pytest.mark.parametrize("failure", ["http", "network"])
def test_telegram_errors_do_not_expose_authenticated_url(workflow, capsys, failure):
    def denied(request, timeout):
        if failure == "http":
            raise urllib.error.HTTPError(request.full_url, 401, BOT_TOKEN, {}, None)
        raise urllib.error.URLError("Network error for " + request.full_url)

    with pytest.raises(SystemExit) as error:
        execute(
            python_script(workflow, "telegram"),
            {"TELEGRAM_BOT_TOKEN": BOT_TOKEN, "TELEGRAM_CHAT_ID": CHAT_ID},
            denied,
        )
    output = capsys.readouterr()
    assert BOT_TOKEN not in str(error.value) + output.out + output.err
    assert "/sendMessage" not in str(error.value) + output.out + output.err


def test_invalid_telegram_token_never_becomes_a_url(workflow):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid token reached the network")

    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        execute(
            python_script(workflow, "telegram"),
            {"TELEGRAM_BOT_TOKEN": "\x16", "TELEGRAM_CHAT_ID": CHAT_ID},
            unexpected,
        )


@pytest.mark.parametrize("job,body", [("openai_auth", "[]"), ("telegram", '{"ok":false}')])
def test_unconfirmed_api_response_is_a_failure(workflow, job, body):
    def unconfirmed(*args, **kwargs):
        return io.BytesIO(body.encode())

    with pytest.raises(SystemExit):
        execute(
            python_script(workflow, job),
            {
                "OPENAI_API_KEY": KEY,
                "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
                "TELEGRAM_CHAT_ID": CHAT_ID,
            },
            unconfirmed,
        )
