# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fidelity contract: what a docker-claude arm sends the model, against a golden capture.

A ``docker-claude`` arm is only a measurement of Claude Code if the request it sends is
the request Claude Code sends. This module records that request and compares it with a
golden capture taken from the real harness:

* :func:`record` runs a ``claude`` client against a local recording server that answers
  ``POST /v1/messages`` with a canned one-token stream. Nothing leaves the machine and no
  model is called, so a capture costs nothing.
* :func:`normalize_request` blanks what changes from run to run or machine to machine:
  dates, versions, ids, the model name, the work directory, the host's platform and shell.
  What is left is the system prompt, the tool list (schemas included), the skill listing
  and the user messages, which is what the golden pins.
* :func:`diff_requests` says where a normalized request differs from the golden.

The arm side is :func:`arm_argv`: it asks the arm's own argv builder for the
``claude -p …`` command line, so the test exercises the flags the arm really passes.

The golden is taken with the pinned Claude Code (:data:`PINNED_CLAUDE_VERSION`) in a
clean container, because a host home carries the operator's own ``CLAUDE.md`` and
reminders that a user's agent in a container never sees. See
``tests/fixtures/fidelity/README.md`` for how to refresh it.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence

from dokimi import process
from dokimi.harnesses.agent_sandbox import agent_env
from dokimi.harnesses.payload_audit import Leak, new_leaks

#: The Claude Code release the golden is captured with; CI installs this one.
PINNED_CLAUDE_VERSION = "2.1.318"

#: The fixed tiny task both golden cases send. Raw, as ``command.py`` sends a task.
FIXTURE_INSTRUCTION = "Fix the off-by-one in sum()."

#: The model the request names. Any model works for the capture; it is normalized away.
FIXTURE_MODEL = "claude-haiku-4-5"

#: Request headers kept in the capture. Session ids and SDK build headers are per run.
KEPT_HEADERS = ("anthropic-beta", "anthropic-version", "x-app", "user-agent")

#: Headers whose values name an API version or a capability, not a per-run value: kept raw
#: (only the model id is blanked), so a change of the version or of a dated beta is a diff.
RAW_HEADERS = ("anthropic-beta", "anthropic-version")

_SSE_EVENTS: Sequence[tuple] = (
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_fixture", "type": "message", "role": "assistant", "model": FIXTURE_MODEL,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "ok"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta",
                       "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 1}}),
    ("message_stop", {"type": "message_stop"}),
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep test output quiet
        return

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        server: "RecordingServer" = self.server  # type: ignore[assignment]
        record: dict[str, Any] = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}
        try:
            record["body"] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            record["body"] = None
        with server.lock:
            server.requests.append(record)
        if "/messages" in self.path and isinstance(record["body"], dict) and record["body"].get("stream"):
            out = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n"
                          for name, data in _SSE_EVENTS).encode("utf-8")
            ctype = "text/event-stream"
        elif "/messages" in self.path:
            out = json.dumps({"id": "msg_fixture", "type": "message", "role": "assistant",
                              "model": FIXTURE_MODEL, "content": [{"type": "text", "text": "ok"}],
                              "stop_reason": "end_turn", "stop_sequence": None,
                              "usage": {"input_tokens": 1, "output_tokens": 1}}).encode("utf-8")
            ctype = "application/json"
        else:
            out, ctype = b"{}", "application/json"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class RecordingServer(ThreadingHTTPServer):
    """A local stand-in for the Messages API that keeps every request it is sent."""

    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1") -> None:
        super().__init__((host, 0), _Handler)
        self.bind_host = host
        self.lock = threading.Lock()
        self.requests: List[dict] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{self.bind_host}:{self.server_address[1]}"

    def __enter__(self) -> "RecordingServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()
        self.server_close()


def record(run: Callable[[str], Any]) -> List[dict]:
    """Run ``run(base_url)`` once against a recording server; return the requests it sent.

    *run* starts the client with ``ANTHROPIC_BASE_URL`` set to *base_url*. Each entry is
    ``{"path", "headers", "body"}``, in the order the requests arrived.
    """
    with RecordingServer() as server:
        run(server.base_url)
        with server.lock:
            return list(server.requests)


def messages_request(records: Iterable[dict]) -> dict:
    """The main turn: the first ``/v1/messages`` record whose body carries tools (the title
    or topic side calls do not). Raises ``ValueError`` when the client sent none."""
    for rec in records:
        body = rec.get("body")
        if "/messages" in rec.get("path", "") and isinstance(body, dict) and body.get("tools"):
            return rec
    raise ValueError("the client sent no /v1/messages request with tools")


def arm_argv(harness: Any, task: Any, *, treatment: Any = None, plugin_dir: Optional[str] = None,
             model: Optional[str] = FIXTURE_MODEL, output_dir: Path) -> List[str]:
    """The ``claude`` argv a docker-claude arm passes after the executable.

    *harness* is a :class:`~dokimi.harnesses.docker_solver.DockerSolverHarness`; the
    argv comes from its own builder, so the flags are the ones the arm sends.
    """
    return harness._claude_args(task, treatment, plugin_dir, model, output_dir)


# -- normalization ----------------------------------------------------------------------

_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?\b")
_MONTH_YEAR = re.compile(rf"\b(?:{_MONTHS}) \d{{4}}\b")
_VERSION_TAG = re.compile(r"(cc_version=|claude-cli/|Claude Code )[0-9][0-9A-Za-z.\-]*")
_MODEL_ID = re.compile(r"\bclaude-[a-z0-9.\-]+\b")
_MODEL_NAME = re.compile(r"named [A-Z][A-Za-z]+ [0-9][0-9.]*")
_IDS = re.compile(r"\b(?:msg|toolu|req|call)_[A-Za-z0-9]{6,}\b")
_PLATFORM = re.compile(r"(Platform: |Shell: |OS Version: )[^\n]*")
_GIT = re.compile(r"Is a git repository: (?:true|false)")


def normalize_text(text: str, *, workdir: str = "", home: str = "") -> str:
    """Blank the run-specific parts of one string. Order matters: paths first."""
    # Claude Code names its per-project memory dir after the cwd, non-alphanumerics as dashes.
    slug = re.sub(r"[^A-Za-z0-9]", "-", workdir)
    for raw, token in ((workdir, "<WORKDIR>"), (slug, "<WORKDIR_SLUG>"),
                       (home, "<HOME>")):
        if raw:
            text = text.replace(raw, token)
    text = _UUID.sub("<UUID>", text)
    text = _HEX64.sub("<HEX>", text)
    text = _IDS.sub("<ID>", text)
    text = _DATE.sub("<DATE>", text)
    text = _MONTH_YEAR.sub("<MONTH YEAR>", text)
    text = _VERSION_TAG.sub(lambda m: m.group(1) + "<VERSION>", text)
    text = _MODEL_NAME.sub("named <MODEL_NAME>", text)
    text = _MODEL_ID.sub("<MODEL>", text)
    text = _PLATFORM.sub(lambda m: m.group(1) + "<HOST>", text)
    text = _GIT.sub("Is a git repository: <GIT>", text)
    return text


def _walk(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {fn(k) if isinstance(k, str) else k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    return value


#: Reminders this build adds to every request (a token budget, the operator's attribution
#: rule); a user's agent in a clean container has neither, so the golden leaves them out.
OPERATOR_REMINDERS = ("<total_tokens>", "Attribution for git commits", "End git commit messages with")
_TOKEN_BUDGET = re.compile(r"\n*<total_tokens>.*?</total_tokens>", re.DOTALL)
#: The operator's commit-attribution rule, as the reminder words it in each build.
_ATTRIBUTION_MARKS = ("Attribution for git commits", "End git commit messages with")
_ATTRIBUTION = re.compile(r"^[^\n]*(?:%s)[^\n]*(?:\n|$)" % "|".join(map(re.escape, _ATTRIBUTION_MARKS)),
                          re.MULTILINE)
_REMINDER_ELEMENT = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_SKILLS_HEADER = "The following skills are available for use with the Skill tool:"


def _keep_listing(text: str, keep_skills: Sequence[str]) -> str:
    """The skill listing with every entry not in *keep_skills* removed, continuation lines
    included: an entry runs from its ``- `` line to the next entry or the next blank line."""
    kept: List[str] = []
    skipping = False
    for ln in text.split("\n"):
        if ln.startswith("- "):
            skipping = not any(n in ln for n in keep_skills)
        elif not ln.strip():
            skipping = False
        if not skipping:
            kept.append(ln)
    return "\n".join(kept)


def _keep_text(text: str, keep_skills: Sequence[str]) -> Optional[str]:
    """The text of one block with the operator extras out; None drops the block.

    Only the operator's own reminder is removed: its ``<system-reminder>`` element, or a bare
    line with its marker, and the delimited token budget. Other text in the same block is
    kept. A block left empty by that is dropped.
    """
    cleaned = _REMINDER_ELEMENT.sub(
        lambda m: "" if any(mk in m.group(0) for mk in _ATTRIBUTION_MARKS) else m.group(0), text)
    cleaned = _ATTRIBUTION.sub("", _TOKEN_BUDGET.sub("", cleaned))
    if cleaned != text and not re.sub(r"</?system-reminder>|\s", "", cleaned):
        return None  # only the reminder's wrapper is left: the block was the reminder
    text = cleaned
    if _SKILLS_HEADER not in text:
        return text
    kept = _keep_listing(text, keep_skills)
    return kept if any(ln.startswith("- ") for ln in kept.split("\n")) else None


def _strip_content(content: Any, keep_skills: Sequence[str]) -> Any:
    if not isinstance(content, list):
        return content
    blocks = []
    for block in content:
        text = block.get("text") if isinstance(block, dict) else None
        if isinstance(text, str):
            new_text = _keep_text(text, keep_skills)
            if new_text is None:
                continue
            block = {**block, "text": new_text}
        blocks.append(block)
    return blocks


def _strip_system(system: Any) -> Any:
    """This build also appends a token budget to the system prompt; drop it."""
    if not isinstance(system, list):
        return system
    return [{**blk, "text": _TOKEN_BUDGET.sub("", blk["text"])}
            if isinstance(blk, dict) and isinstance(blk.get("text"), str) else blk
            for blk in system]


def _strip_operator_extras(body: dict, keep_skills: Sequence[str]) -> dict:
    """Drop :data:`OPERATOR_REMINDERS` and every skill-listing entry but *keep_skills*."""
    out = dict(body)
    if "system" in out:
        out["system"] = _strip_system(out["system"])
    out["messages"] = [{**msg, "content": _strip_content(msg.get("content"), keep_skills)}
                       for msg in body.get("messages", [])]
    return out


def normalize_request(record: dict, *, workdir: str = "", home: str = "",
                      keep_skills: Sequence[str] = ("sum-fast",)) -> dict:
    """The golden-comparable form of one recorded request.

    Keeps the body (model, system, tools, messages, thinking and context settings) and the
    :data:`KEPT_HEADERS`; the body's ``metadata.user_id`` carries per-run ids, so it is
    normalized like any string. Dates, versions, ids and host facts are blanked.
    """
    def fn(text: str) -> str:
        return normalize_text(text, workdir=workdir, home=home)

    body = _strip_operator_extras(dict(record.get("body") or {}), keep_skills)
    headers = {k: v for k, v in (record.get("headers") or {}).items() if k in KEPT_HEADERS}
    kept = {k: (_MODEL_ID.sub("<MODEL>", v) if k in RAW_HEADERS else _walk(v, fn))
            for k, v in headers.items()}
    return {"headers": kept, **_walk(body, fn)}


# -- golden I/O and comparison ----------------------------------------------------------

def load_golden(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_golden(path: Path, request: dict, *, version: str, case: str) -> None:
    """Write one golden case. Sorted keys and a trailing newline keep the diff of a refresh readable."""
    doc = {
        "_meta": {
            "claude_code_version": version,
            "case": case,
            "instruction": FIXTURE_INSTRUCTION,
            "normalized": "dates, versions, ids, model, work dir and host facts blanked by "
                          "dokimi.harnesses.fidelity.normalize_request",
            "refresh": "tests/fixtures/fidelity/README.md",
        },
        "request": request,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def diff_requests(actual: dict, golden: dict, *, max_lines: int = 60) -> List[str]:
    """A unified diff of two normalized requests, empty when they are equal."""
    if actual == golden:
        return []
    a = json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    g = json.dumps(golden, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    lines = list(difflib.unified_diff(g, a, "golden", "arm", lineterm="", n=2))
    return lines[:max_lines] + ([f"... {len(lines) - max_lines} more diff lines"]
                                if len(lines) > max_lines else [])


def leaks_against(actual: dict, golden: dict, allowed: Iterable[str] = ()) -> List[Leak]:
    """Markers in the arm's request that the golden does not already have (see payload_audit)."""
    return new_leaks(actual, golden, allowed=list(allowed))


def residue(value: Any) -> List[str]:
    """Run-specific text left in a normalized request: dates, UUIDs, versions, host paths.

    The golden must have none, so a refresh that captured a dirty value fails the unit test.
    """
    found: List[str] = []
    if isinstance(value, dict) and isinstance(value.get("headers"), dict):
        # The raw headers are API versions and capability names: their dates are the point.
        value = {**value, "headers": {k: v for k, v in value["headers"].items() if k not in RAW_HEADERS}}

    def fn(text: str) -> str:
        for name, pat in (("date", _DATE), ("uuid", _UUID), ("hex", _HEX64), ("version", _VERSION_TAG),
                          ("model", _MODEL_ID), ("abs-path", re.compile(r"/(?:tmp|mnt|localdisk|nfs|home|root|var)/"))):
            if pat.search(text):
                found.append(f"{name}: {text[:80]!r}")
        return text

    _walk(value, fn)
    return found


# -- runners ----------------------------------------------------------------------------

#: Non-root uid the container runs the client as: ``--dangerously-skip-permissions`` refuses root.
_CONTAINER_HOME = "/home/agent"


def client_env(base_url: str, home: str, config: str) -> dict:
    """The client's environment: the arm's sealed skill switches (:func:`agent_env`), no
    operator variables, the recording server as the API."""
    return agent_env({}, {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": home,
        "CLAUDE_CONFIG_DIR": config,
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": "sk-fidelity-not-a-key",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    })


def run_host(binary: str, args: List[str], *, workdir: Path, scratch: Path,
             timeout: float = 300.0) -> Callable[[str], None]:
    """A runner for :func:`record` that runs *binary* on this host, in *workdir*."""
    def run(base_url: str) -> None:
        home = scratch / "home"
        config = home / "config"
        config.mkdir(parents=True, exist_ok=True)
        env = client_env(base_url, str(home), str(config))
        process.run([binary, *args], timeout=timeout, env=process.Env.only(set=env), cwd=str(workdir))

    return run


def run_docker(image: str, binary: str, args: List[str], *, workdir: Path, plugin_dir: Optional[Path],
               uid: int, gid: int, timeout: float = 300.0) -> Callable[[str], None]:
    """A runner for :func:`record` that runs the client in a clean container.

    The binary, the work dir and the plugin dir are mounted at their host paths, so the
    argv and the cwd the model sees are the ones :func:`run_host` gives. The home is a
    tmpfs, so nothing from the operator's host reaches the request.
    """
    def run(base_url: str) -> None:
        mounts = ["-v", f"{binary}:/opt/agent/claude:ro", "-v", f"{workdir}:{workdir}"]
        if plugin_dir is not None:
            mounts += ["-v", f"{plugin_dir}:{plugin_dir}:ro"]
        env = client_env(base_url, _CONTAINER_HOME, f"{_CONTAINER_HOME}/config")
        env_args: List[str] = []
        for key, value in env.items():
            env_args += ["-e", f"{key}={value}"]
        cmd = ["docker", "run", "--rm", "--network", "host", "-u", f"{uid}:{gid}",
               "--tmpfs", f"{_CONTAINER_HOME}:uid={uid},gid={gid}", "-w", str(workdir),
               *mounts, *env_args, image, "/opt/agent/claude", *args]
        process.run(cmd, timeout=timeout, env=process.Env.inherit())

    return run


# -- one arm, end to end ----------------------------------------------------------------

#: The two golden cases: the arm with no skill, and the arm with the fixture skill.
CASES = ("bare", "skill")
FIXTURE_SKILL = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fidelity" / "skill" / "sum-fast"


def capture_arm(case: str, *, binary: str, scratch: Path, image: Optional[str] = None,
                uid: int = 1000, gid: int = 1000) -> dict:
    """The normalized request a docker-claude arm sends for *case* (``bare`` or ``skill``).

    *binary* is the client to run; *image* runs it in a clean container (the golden is taken
    that way), ``None`` runs it on this host. The arm's own argv builder and skill plugin
    build are used, so the request is the one :class:`DockerSolverHarness` produces, minus
    the container's own paths, which are the same here.
    """
    from dokimi.harnesses.docker_solver import DockerSolverHarness
    from dokimi.harnesses.skill_treatment import build_plugin
    from dokimi.harnesses.tasks import TaskSpec

    if case not in CASES:
        raise ValueError(f"unknown case {case!r}; expected one of {CASES}")
    scratch = scratch.resolve()  # the client sees the real path (a /tmp symlink, say)
    workdir = scratch / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    task = TaskSpec(name="fidelity-sum", path=scratch / "task", instruction=FIXTURE_INSTRUCTION)
    harness = DockerSolverHarness(harness_id="docker-claude", skill_notice=(case == "skill"))
    treatment = plugin_dir = None
    plugin = None
    if case == "skill":
        plugin = scratch / "plugin"
        plugin.mkdir(parents=True, exist_ok=True)
        treatment = build_plugin(FIXTURE_SKILL, plugin)
        plugin_dir = str(plugin)
    args = arm_argv(harness, task, treatment=treatment, plugin_dir=plugin_dir,
                    model=FIXTURE_MODEL, output_dir=scratch)
    if image:
        runner = run_docker(image, binary, args, workdir=workdir,
                            plugin_dir=plugin, uid=uid, gid=gid)
        home = _CONTAINER_HOME
    else:
        runner = run_host(binary, args, workdir=workdir, scratch=scratch)
        home = str(scratch / "home")
    turn = messages_request(record(runner))
    return normalize_request(turn, workdir=str(workdir), home=home)
