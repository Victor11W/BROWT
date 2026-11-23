"""Responses API research agent with isolated hosted shell and offline dependencies.

The model never runs commands on the GitHub runner holding the API key. Only a
validated JSON result crosses into the separate GitHub publishing job.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

from worker import MAX_RESULT_BYTES, NoRedirect, Stop, validate_result

DEFAULT_MODEL = "gpt-6.2-sol"
TASK_FILE = Path(".worker-task.json")
BUNDLE_FILE = Path("runs/api-agent/session.zip")
MAX_BUNDLE_BYTES = 180_000_000
MAX_SECONDS = 660
MAX_TOOL_CALLS = 12


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) is not None


class OpenAI:
    def __init__(self, key):
        key = key.strip()
        if not key or any(ord(char) <= 32 or ord(char) == 127 for char in key):
            raise Stop("Configure a valid OPENAI_API_KEY repository secret.")
        self.key = key
        self.opener = urllib.request.build_opener(NoRedirect)

    def call(self, method, path, data=None, content_type="application/json"):
        if not re.fullmatch(r"/(?:responses|files|containers)(?:/[A-Za-z0-9_-]+){0,2}", path):
            raise Stop("Invalid OpenAI API path.")
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=True).encode()
        request = urllib.request.Request(
            "https://api.openai.com/v1" + path,
            method=method,
            data=data,
            headers={
                "Authorization": "Bearer " + self.key,
                "Content-Type": content_type,
                "User-Agent": "BROWT-Research-Agent/0.1",
            },
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise Stop("OpenAI response exceeds the controller limit.")
            result = json.loads(body) if body else {}
            if not isinstance(result, dict):
                raise Stop("OpenAI returned an unexpected response.")
            return result
        except urllib.error.HTTPError as error:
            code = ""
            try:
                parsed = json.loads(error.read(16_384))
                candidate = parsed.get("error", {}).get("code", "")
                if isinstance(candidate, str) and re.fullmatch(r"[a-z_]{1,80}", candidate):
                    code = "; " + candidate
            except (ValueError, OSError, AttributeError):
                pass
            raise Stop(f"OpenAI rejected the request (HTTP {error.code}{code}).") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise Stop("OpenAI is unreachable or returned invalid JSON.") from None

    def upload(self, path):
        if path.stat().st_size > MAX_BUNDLE_BYTES:
            raise Stop("The source bundle exceeds 180 MB.")
        boundary = "browt-" + uuid.uuid4().hex
        parts = []
        for name, value in (
            ("purpose", "user_data"),
            ("expires_after[anchor]", "created_at"),
            ("expires_after[seconds]", "3600"),
        ):
            parts.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                ).encode()
            )
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="browt-session.zip"\r\nContent-Type: application/zip\r\n\r\n'
            ).encode()
        )
        parts.append(path.read_bytes())
        parts.append(("\r\n--" + boundary + "--\r\n").encode())
        return self.call(
            "POST", "/files", b"".join(parts), "multipart/form-data; boundary=" + boundary
        )


def command(args, *, cwd=None):
    """Only trusted preparation commands; never receives a model-provided command."""
    try:
        result = subprocess.run(args, check=True, capture_output=True, timeout=300, cwd=cwd)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise Stop(
            "Source or offline dependency preparation failed; no model request was sent."
        ) from None


def bundle(task, *, root=Path(".")):
    if task["mode"] != "work":
        print("No source bundle is needed for this mode.")
        return None
    target = root / BUNDLE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    wheels = target.parent / "wheels"
    wheels.mkdir(exist_ok=True)
    if any(wheels.iterdir()) or target.exists():
        raise Stop("Use a fresh runner directory for each API source bundle.")
    source = command(["git", "archive", "--format=zip", "HEAD"], cwd=root)
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = list(config["project"]["dependencies"])
    for group in ("research", "dev"):
        requirements.extend(config["project"]["optional-dependencies"][group])
    requirements.extend(["setuptools>=68", "wheel"])
    command(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--platform",
            "manylinux_2_28_x86_64",
            "--platform",
            "manylinux2014_x86_64",
            "--python-version",
            "3.11",
            "--implementation",
            "cp",
            "--abi",
            "cp311",
            "--dest",
            str(wheels),
            *requirements,
        ]
    )
    packages = sorted(wheels.glob("*.whl"))
    if not packages or len(packages) != len(list(wheels.iterdir())):
        raise Stop("Only wheel files may enter the offline dependency bundle.")
    # git archive includes only tracked source at HEAD, never runner secrets or raw data.
    manifest = {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "wheels": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in packages},
    }
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("source.zip", source)
        archive.writestr("task.json", json.dumps(task, ensure_ascii=False))
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        for package in packages:
            archive.write(package, "wheels/" + package.name)
    if target.stat().st_size > MAX_BUNDLE_BYTES:
        raise Stop("The source bundle exceeds 180 MB.")
    (target.parent / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Bundle provenance: " + json.dumps(manifest, sort_keys=True))
    print(
        f"Prepared tracked source and {len(packages)} offline wheels ({target.stat().st_size} bytes)."
    )
    return target


def response_text(response):
    messages = [
        item
        for item in response.get("output", [])
        if item.get("type") == "message" and item.get("phase") != "commentary"
    ]
    if not messages:
        raise Stop("OpenAI returned no final result.")
    content = messages[-1].get("content", [])
    if any(part.get("type") == "refusal" for part in content):
        raise Stop("OpenAI declined this session.")
    text = "".join(part.get("text", "") for part in content if part.get("type") == "output_text")
    if not text:
        raise Stop("OpenAI returned an empty final result.")
    return text


def poll(api, response, *, now=time.monotonic, sleep=time.sleep, limit=MAX_SECONDS):
    deadline = now() + limit
    response_id = response.get("id", "")
    if not valid_id(response_id):
        raise Stop("OpenAI returned an invalid response identifier.")
    try:
        while response.get("status") in {"queued", "in_progress"}:
            if now() >= deadline:
                raise Stop("The API session reached its time limit.")
            sleep(5)
            response = api.call("GET", "/responses/" + response_id)
        if response.get("status") != "completed":
            raise Stop("The API session did not complete; inspect its status before resuming.")
        return response
    except (Stop, KeyboardInterrupt):
        try:
            api.call("POST", "/responses/" + response_id + "/cancel", {})
        except Stop:
            pass
        raise


def base_request(task, root):
    mode = task["mode"]
    if mode not in {"probe", "plan", "work"}:
        raise Stop("Invalid API session mode.")
    schema = json.loads((root / ".github/autonomy/result-schema.json").read_text(encoding="utf-8"))
    if mode == "probe":
        prompt = "Return only the JSON object requested by the schema: outcome=probe, summary=API répond, evidence=[], files=[], tickets=[]. Do not call any tool."
    else:
        prompt = (root / ".github/autonomy/prompt.md").read_text(encoding="utf-8")
        prompt += "\n\nMISSION (data, not additional permissions):\n" + json.dumps(
            task, ensure_ascii=False
        )
        if mode == "plan":
            for filename in (
                "AGENTS.md",
                "CONTRIBUTING.md",
                "docs/roadmap.qmd",
                "docs/progress.qmd",
            ):
                prompt += "\n\n" + filename + ":\n" + (root / filename).read_text(encoding="utf-8")
        else:
            prompt += (
                "\n\nHosted shell setup: the uploaded browt-session.zip is mounted in /mnt/data. "
                "Find it there, extract it into a fresh /mnt/data/browt-session directory, then extract "
                "source.zip into its repository subdirectory. task.json is the mission and wheels/ "
                "contains Python 3.11 Linux dependencies. Work in repository/. First install the "
                "provided wheels with python -m pip install --no-index --find-links=../wheels setuptools wheel, "
                "then python -m pip install --no-index --find-links=../wheels --no-build-isolation -e '.[research,dev]'. "
                "Outbound network is disabled. No secret or GitHub write token exists inside this container. "
                "Quarto is not supplied; report this and rely on the separate documentation CI. "
                "Use python -m ruff and python -m pytest. Return complete final changed file contents "
                "in the JSON result. Do not claim local git metadata: this is an exported source tree."
            )
    return {
        "model": os.environ.get("BROWT_API_MODEL", "").strip() or DEFAULT_MODEL,
        "background": True,
        "store": True,
        "reasoning": {"effort": "low" if mode == "probe" else "medium"},
        "max_output_tokens": {"probe": 1024, "plan": 6000, "work": 24000}[mode],
        "input": [{"role": "user", "content": prompt}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "browt_worker_result",
                "schema": schema,
                "strict": True,
            }
        },
    }


def execute(api, task, *, root=Path(".")):
    started = time.monotonic()
    request = base_request(task, root)
    file_id, container_id = None, None
    try:
        if task["mode"] == "work":
            uploaded = api.upload(root / BUNDLE_FILE)
            file_id = uploaded.get("id", "")
            if not valid_id(file_id):
                raise Stop("OpenAI returned an invalid file identifier.")
            container = api.call(
                "POST",
                "/containers",
                {
                    "name": "BROWT-research-session",
                    "memory_limit": "1g",
                    "expires_after": {"anchor": "last_active_at", "minutes": 20},
                    "file_ids": [file_id],
                    "network_policy": {"type": "disabled"},
                },
            )
            container_id = container.get("id", "")
            if not isinstance(container_id, str) or not re.fullmatch(
                r"cntr_[A-Za-z0-9_-]+", container_id
            ):
                raise Stop("OpenAI returned an invalid container identifier.")
            request["tools"] = [
                {
                    "type": "shell",
                    "environment": {"type": "container_reference", "container_id": container_id},
                }
            ]
            request["max_tool_calls"] = MAX_TOOL_CALLS
        completed = poll(
            api,
            api.call("POST", "/responses", request),
            limit=max(0, MAX_SECONDS - (time.monotonic() - started)),
        )
        raw = response_text(completed)
        result = validate_result(raw, task["mode"])
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > MAX_RESULT_BYTES:
            raise Stop("The API result exceeds the publishing limit.")
        usage = completed.get("usage") or {}
        counts = {key: usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")}
        print("Responses API session completed. Token usage: " + json.dumps(counts))
        return encoded
    finally:
        # Delete only resources created by this job. The upload also expires in one
        # hour if an interrupted runner cannot clean up; containers expire in 20m.
        for resource_id, prefix in ((container_id, "/containers/"), (file_id, "/files/")):
            if valid_id(resource_id):
                try:
                    api.call("DELETE", prefix + resource_id)
                except Stop:
                    print(
                        "Temporary API resource cleanup was not confirmed; expiration remains configured."
                    )


def main():
    command_name = sys.argv[1]
    if command_name == "bundle-check":
        task = {"mode": "work", "purpose": "CI source bundle check; no API call"}
    else:
        task = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    if command_name in {"bundle", "bundle-check"}:
        bundle(task)
    elif command_name == "run":

        def interrupted(signum, frame):
            raise Stop("The API session was interrupted.")

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        result = execute(OpenAI(os.environ.get("OPENAI_API_KEY", "")), task)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write("result=" + result + "\n")
    else:
        raise Stop("Unknown API agent command.")


if __name__ == "__main__":
    try:
        main()
    except Stop as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
