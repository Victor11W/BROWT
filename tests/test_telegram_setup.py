from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

TOKEN = "123456789:" + "A" * 35
CHAT_ID = 1234567890


@pytest.fixture
def helper(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts" / "telegram_setup.py"
    spec = importlib.util.spec_from_file_location("telegram_setup", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "is_windows", lambda: False)
    monkeypatch.setattr(module.getpass, "getpass", lambda _: TOKEN)
    monkeypatch.setattr("builtins.input", lambda _: "")

    def unexpected_network(*args, **kwargs):
        pytest.fail("An unexpected network request was attempted")

    monkeypatch.setattr(module.urllib.request, "urlopen", unexpected_network)
    return module


def response(updates):
    return io.BytesIO(json.dumps({"ok": True, "result": updates}).encode())


def start(chat_id=CHAT_ID, kind="private"):
    return {"message": {"chat": {"type": kind, "id": chat_id}, "text": "/start"}}


@pytest.mark.parametrize("token", ["\x16", TOKEN + "\x16", "", " ", "sk-example", TOKEN + " extra"])
def test_invalid_token_never_reaches_network_or_output(helper, monkeypatch, capsys, token):
    monkeypatch.setattr(helper.getpass, "getpass", lambda _: token)
    assert helper.main(["--manual"]) == 1
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "\x16" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_windows_clipboard_to_chat_id_without_terminal_paste(helper, monkeypatch, capsys):
    monkeypatch.setattr(helper, "is_windows", lambda: True)
    calls = []

    def clipboard_process(args, **kwargs):
        calls.append((args, kwargs))
        if args[-1] == "Get-Clipboard -Raw":
            return SimpleNamespace(stdout=TOKEN + "\r\n")
        assert args[-1] == "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
        assert kwargs["input"] == str(CHAT_ID)
        return SimpleNamespace(stdout="")

    def no_password_prompt(_):
        pytest.fail("Windows default must not ask for a terminal paste")

    requests = []

    def telegram(request, **kwargs):
        requests.append((request, kwargs))
        return response([start(), start(), start(-999, "group")])

    monkeypatch.setattr(helper.subprocess, "run", clipboard_process)
    monkeypatch.setattr(helper.getpass, "getpass", no_password_prompt)
    monkeypatch.setattr(helper.urllib.request, "urlopen", telegram)
    assert helper.main([]) == 0
    assert len(calls) == 2
    assert len(requests) == 1
    request, options = requests[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.telegram.org/bot" + TOKEN + "/getUpdates"
    assert json.loads(request.data) == {"timeout": 0, "allowed_updates": ["message"]}
    assert options["timeout"] == 20
    for args, kwargs in calls:
        assert TOKEN not in repr(args)
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["check"] is True
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "Identifiant copié" in captured.out


def test_clipboard_failure_does_not_expose_process_output(helper, monkeypatch, capsys):
    monkeypatch.setattr(helper, "is_windows", lambda: True)

    def denied(*args, **kwargs):
        raise subprocess.CalledProcessError(1, TOKEN, output=TOKEN, stderr=TOKEN)

    monkeypatch.setattr(helper.subprocess, "run", denied)
    assert helper.main([]) == 1
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "presse-papiers" in captured.out


def test_multiple_private_chats_are_not_selected_automatically(helper, monkeypatch, capsys):
    monkeypatch.setattr(helper, "is_windows", lambda: True)
    clipboard_calls = []

    def clipboard(command, *, value=None):
        clipboard_calls.append(command)
        return TOKEN

    monkeypatch.setattr(helper, "powershell_clipboard", clipboard)
    monkeypatch.setattr(
        helper.urllib.request,
        "urlopen",
        lambda *args, **kwargs: response([start(2), start(1), start(2)]),
    )
    assert helper.main([]) == 0
    assert clipboard_calls == ["Get-Clipboard -Raw"]
    captured = capsys.readouterr()
    assert "\n1\n2\n" in captured.out
    assert "Identifiant copié" not in captured.out


def test_manual_mode_on_windows_avoids_clipboard(helper, monkeypatch):
    monkeypatch.setattr(helper, "is_windows", lambda: True)

    def unexpected_clipboard(*args, **kwargs):
        pytest.fail("Manual mode must not access the clipboard")

    monkeypatch.setattr(helper, "powershell_clipboard", unexpected_clipboard)
    monkeypatch.setattr(helper.urllib.request, "urlopen", lambda *a, **k: response([start()]))
    assert helper.main(["--manual"]) == 0


def test_http_error_never_displays_token_or_url(helper, monkeypatch, capsys):
    def unauthorized(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.telegram.org/bot" + TOKEN + "/getUpdates",
            401,
            TOKEN,
            {},
            None,
        )

    monkeypatch.setattr(helper.urllib.request, "urlopen", unauthorized)
    assert helper.main([]) == 1
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "HTTP 401" in captured.out


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"ok": False},
        {"ok": True, "result": None},
        {"ok": True, "result": [start(1, "group"), {"message": {"text": "hello"}}]},
    ],
)
def test_invalid_or_irrelevant_response_has_no_traceback(helper, monkeypatch, capsys, body):
    monkeypatch.setattr(
        helper.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode()),
    )
    assert helper.main([]) == 1
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_failed_clipboard_write_preserves_successful_chat_lookup(helper, monkeypatch, capsys):
    monkeypatch.setattr(helper, "is_windows", lambda: True)

    def clipboard(command, *, value=None):
        if value is None:
            return TOKEN
        raise RuntimeError("Clipboard unavailable")

    monkeypatch.setattr(helper, "powershell_clipboard", clipboard)
    monkeypatch.setattr(helper.urllib.request, "urlopen", lambda *a, **k: response([start()]))
    assert helper.main([]) == 0
    captured = capsys.readouterr()
    assert str(CHAT_ID) in captured.out
    assert "copie automatique a échoué" in captured.out
    assert TOKEN not in captured.out + captured.err


def test_clipboard_is_only_read_after_user_copies_token(helper, monkeypatch):
    monkeypatch.setattr(helper, "is_windows", lambda: True)
    ready = []

    def copied(_):
        ready.append(True)
        return ""

    def clipboard(command, *, value=None):
        assert ready, "Clipboard read before the user had time to copy the token"
        return TOKEN if value is None else ""

    monkeypatch.setattr("builtins.input", copied)
    monkeypatch.setattr(helper, "powershell_clipboard", clipboard)
    monkeypatch.setattr(helper.urllib.request, "urlopen", lambda *a, **k: response([start()]))
    assert helper.main([]) == 0


def test_invalid_clipboard_does_not_make_a_request(helper, monkeypatch, capsys):
    monkeypatch.setattr(helper, "is_windows", lambda: True)
    monkeypatch.setattr(helper, "powershell_clipboard", lambda command: "python telegram_setup.py")
    assert helper.main([]) == 1
    assert "Copiez uniquement le jeton" in capsys.readouterr().out
