#!/usr/bin/env python3
"""Live preflight. Real HTTP and Gemini calls against the running server."""

from __future__ import annotations

import base64
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from build import NOTES, ROOT, write_graph
from server import HOST, PORT, VIEWER, api_keys, load_config

BASE = f"http://{HOST}:{PORT}"
EXPECTED_MODEL = "gemini-2.5-flash"
CHAT_TIMEOUT = 120
# 1x1 baseline JPEG. A real file, not a stand-in for the HTTP call.
PROBE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxISEhUSEhIVFhUVGBcYGBgYGBgYGBgYGBgY"
    "GBgYGBgYHSggGBolHRgVITEhJSkrLi4uFx8zODMtNygtLisBCgoKDg0OGhAQGy0lHyUtLS0t"
    "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAQAB"
    "AQMBIgACEQEDEQH/xAAbAAABBQEBAAAAAAAAAAAAAAADAAIEBQYBB//EABYBAQEBAAAAAAAA"
    "AAAAAAAAAAABAgP/2gAMAwEAAhEDEQA/APr6AAP/2Q=="
)
KEY_RE = re.compile(r"AIza[\w\-]+")
ERROR_ANSWER = (
    "No Gemini API key is configured",
    "The Gemini API key was rejected",
    "The model returned no text",
    "That was not a JPEG",
    "The question was empty",
)

results: list[str] = []


def _out(text: str) -> None:
    print(text, flush=True)


def record(status: str, label: str, detail: str = "") -> None:
    mark = {"pass": "\u2713", "fail": "\u2717", "warn": "!"}[status]
    line = f"{mark} {label}"
    if detail:
        line = f"{line} — {detail}"
    _out(line)
    results.append(status)


def safe_text(exc: BaseException) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    text = KEY_RE.sub("AIza…", text)
    return text.replace("\n", " ")[:240]


def http(
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        BASE + path,
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post_json(path: str, payload: dict, timeout: float = CHAT_TIMEOUT) -> tuple[int, dict]:
    status, raw = http(
        "POST",
        path,
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
        timeout,
    )
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return status, data


def gemini_once(api_key: str, model: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents="Reply with the single word pong.",
            config=types.GenerateContentConfig(
                system_instruction="Reply with the single word pong."
            ),
        )
        text = getattr(response, "text", None) or ""
        return text.strip()
    finally:
        close = getattr(client, "close", None)
        if close:
            close()


def short_reason(exc: BaseException) -> str:
    text = safe_text(exc)
    match = re.search(r"'message': '([^']+)'", text)
    if match:
        return match.group(1)[:80]
    return text[:80]


def rate_limited(exc: BaseException) -> bool:
    text = safe_text(exc).lower()
    return any(
        word in text
        for word in ("429", "resource_exhausted", "quota", "rate limit", "too many requests")
    )


def check_server() -> tuple[str, str]:
    status, raw = http("GET", "/")
    text = raw.decode("utf-8", errors="replace")
    if status != 200:
        return "fail", f"GET / returned {status}"
    if "Jarvis Second Brain" not in text:
        return "fail", "GET / was 200 but it is not the viewer"
    return "pass", f"GET / 200, {len(raw)} bytes"


def check_graph() -> tuple[str, str]:
    status, raw = http("GET", "/graph-data.js")
    if status != 200:
        return "fail", f"GET /graph-data.js returned {status}"
    text = raw.decode("utf-8")
    prefix = "const GRAPH = "
    if not text.startswith(prefix):
        return "fail", "graph-data.js did not start with const GRAPH"
    payload = text[len(prefix) :].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    data = json.loads(payload)
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or not nodes:
        return "fail", "node count is 0"
    return "pass", f"{len(nodes)} nodes"


def answer_ok(data: dict) -> str | None:
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return "answer was empty"
    if answer.startswith(ERROR_ANSWER):
        return answer.split(".")[0]
    return None


def check_chat() -> tuple[str, str]:
    status, data = post_json(
        "/chat",
        {"message": "What does the cash flow forecast leave room for?"},
    )
    if status != 200:
        return "fail", f"POST /chat returned {status}"
    problem = answer_ok(data)
    if problem:
        return "fail", problem
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return "fail", "nodes was not an array"
    if not nodes:
        return "fail", "nodes array was empty"
    if not all(isinstance(item, int) for item in nodes):
        return "fail", "nodes array contained a non-integer"
    return "pass", f"{len(nodes)} nodes"


def check_keys() -> tuple[str, str]:
    keys = api_keys(load_config())
    if not keys:
        return "fail", "no keys in config.json"
    model = str(load_config().get("model") or EXPECTED_MODEL)
    rejected = []
    limited = []
    empty = []
    for index, key in enumerate(keys, start=1):
        try:
            reply = gemini_once(key, model)
        except Exception as exc:
            if rate_limited(exc):
                limited.append(str(index))
            else:
                rejected.append(f"{index} ({short_reason(exc)})")
            continue
        if not reply:
            empty.append(str(index))
    if rejected or empty:
        parts = []
        if rejected:
            parts.append("rejected " + ", ".join(rejected))
        if empty:
            parts.append("empty reply from " + ", ".join(empty))
        return "fail", f"{len(keys)} keys called; " + "; ".join(parts)
    if limited:
        return "warn", f"{len(keys)} keys called; rate-limited: " + ", ".join(limited)
    return "pass", f"{len(keys)} keys accepted"


def check_model() -> tuple[str, str]:
    config = load_config()
    model = str(config.get("model") or "")
    if model != EXPECTED_MODEL:
        return "fail", f"config names {model or 'nothing'}, not {EXPECTED_MODEL}"
    keys = api_keys(config)
    if not keys:
        return "fail", "no key available to reach the model"
    try:
        reply = gemini_once(keys[0], model)
    except Exception as exc:
        status = "warn" if rate_limited(exc) else "fail"
        return status, safe_text(exc)
    if not reply:
        return "fail", "model returned an empty reply"
    return "pass", f"{model} replied"


def check_remember() -> tuple[str, str]:
    token = uuid.uuid4().hex[:8]
    fact = f"preflight marker {token} belongs in the index"
    status, data = post_json("/remember", {"message": f"remember that {fact}"})
    if status != 200 or not data.get("ok"):
        return "fail", data.get("answer") or f"POST /remember returned {status}"
    node = data.get("node") or {}
    rel = str(node.get("path") or "")
    path = ROOT / rel if rel else None
    if path is None or not path.is_file():
        return "fail", "remember returned ok but the file is not on disk"
    if token not in path.read_text(encoding="utf-8"):
        return "fail", "the file does not contain the marker"
    chat_status, chat = post_json(
        "/chat",
        {"message": f"Where should preflight marker {token} live?"},
    )
    problem = answer_ok(chat) if chat_status == 200 else f"POST /chat returned {chat_status}"
    nodes = chat.get("nodes")
    retrieved = (
        problem is None
        and isinstance(nodes, list)
        and node.get("id") in nodes
    )
    leftover = _remove_probe(path)
    if not retrieved:
        detail = problem or "the new note was not in the next /chat nodes array"
        return "fail", detail
    if leftover:
        return "warn", f"retrieved node {node.get('id')}, but {leftover}"
    return "pass", f"wrote {path.name} and /chat returned node {node.get('id')}"


def _remove_probe(path: Path) -> str:
    try:
        if path.is_file():
            path.unlink()
        folder = NOTES / "captures"
        if folder.is_dir() and not any(folder.glob("*.md")):
            folder.rmdir()
        write_graph()
    except OSError as exc:
        return safe_text(exc)
    return ""


def check_see() -> tuple[str, str]:
    if not PROBE_JPEG.startswith(b"\xff\xd8"):
        return "fail", "probe image is not a JPEG"
    status, raw = http(
        "POST",
        "/see",
        PROBE_JPEG,
        {
            "Content-Type": "image/jpeg",
            "X-Question": urllib.parse.quote("what am I looking at"),
        },
        CHAT_TIMEOUT,
    )
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return "fail", f"POST /see returned {status} and no JSON"
    if status != 200:
        return "fail", data.get("answer") or f"POST /see returned {status}"
    problem = answer_ok(data if isinstance(data, dict) else {})
    if problem:
        return "fail", problem
    answer = str(data.get("answer") or "").replace("\n", " ")
    return "pass", answer[:120]


def check_served_files() -> tuple[str, str]:
    files = [path for path in VIEWER.rglob("*") if path.is_file()]
    if not files:
        return "fail", "viewer directory has no files"
    mismatched = []
    for path in files:
        rel = path.relative_to(VIEWER).as_posix()
        url_path = "/" if rel == "index.html" else "/" + rel
        status, raw = http("GET", url_path)
        disk = path.read_bytes()
        if status != 200 or raw != disk:
            mismatched.append(rel)
    if mismatched:
        return "fail", "mismatch: " + ", ".join(mismatched)
    return "pass", f"{len(files)} files match"


def check_config_hidden() -> tuple[str, str]:
    paths = (
        "/config.json",
        "/config",
        "/../config.json",
        "/google_refresh_token.json",
        "/oauth/start",
        "/oauth/callback",
    )
    for path in paths:
        status, raw = http("GET", path)
        text = raw.decode("utf-8", errors="replace")
        if status == 200 or "gemini_api_keys" in text or "refresh_token" in text:
            return "fail", f"{path} returned {status}"
    return "pass", "config, token, and OAuth paths are not served on 4701"


def check_google_status() -> tuple[str, str]:
    code, raw = http("GET", "/google/status")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return "fail", f"GET /google/status returned {code} and no JSON"
    if code != 200 or not isinstance(payload, dict) or "connected" not in payload:
        return "fail", f"GET /google/status returned {code}"
    allowed = {"connected", "gmail", "calendar", "drive", "docs"}
    extra = set(payload) - allowed
    if extra:
        return "fail", "GET /google/status returned extra fields"
    if any(not isinstance(payload.get(key), bool) for key in payload):
        return "fail", "GET /google/status returned a non-boolean flag"
    return "pass", f"connected={payload['connected']}"


def run(label: str, check) -> None:
    try:
        status, detail = check()
    except Exception as exc:
        status, detail = "fail", safe_text(exc)
    record(status, label, detail)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _out(f"Preflight {BASE}")
    run("The server is up and serving the viewer.", check_server)
    run("The graph data loads and the node count is greater than zero.", check_graph)
    run("/chat returns a well-formed answer to a real question, with a nodes array.", check_chat)
    run("The gemini_api_keys in config.json are valid, by one real minimal call per key.", check_keys)
    run(f"The model {EXPECTED_MODEL} named in config.json is reachable.", check_model)
    run("/remember writes a real file and the note is retrievable by /chat immediately afterwards.", check_remember)
    run("/see answers a real JPEG.", check_see)
    run("The files the browser is actually served match the files on disk.", check_served_files)
    run("config.json is not reachable from the browser.", check_config_hidden)
    run("GET /google/status returns only a connected flag.", check_google_status)
    passed = results.count("pass")
    failed = results.count("fail")
    warned = results.count("warn")
    _out(f"{passed} pass, {failed} fail, {warned} warn")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(1)
