# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Repeated runs of one task -> how stable the result was, how stable the path was, and whether
the path kept the rules the task sets. Three questions, three answers; never one score.

**Result.** A run passes when its outcome says so: an ``--outcomes`` file (trace -> bool, from a
verifier that checked the workspace) or, without one, the harness's own ``result`` event
(``subtype == "success"`` and not ``is_error``), which only says the agent stopped cleanly, not that
the task is done. ``pass@k`` is the chance at least one of ``k`` runs drawn from the ``n`` passes,
``pass^k`` the chance all ``k`` do (τ-bench); both are the unbiased estimates ``1 - C(n-c,k)/C(n,k)``
and ``C(c,k)/C(n,k)`` over ``c`` passes.

**Path.** Each run becomes a sequence of main-thread tool calls at two grains: ``kind`` (the tool's
name; ``Bash`` gets its program, so ``Bash:pytest`` and ``Bash:git`` differ) and ``action`` (kind plus
the call's target: a file's name, a search pattern). Over every pair of runs:

* ``mix_jsd`` – Jensen-Shannon divergence (base 2, 0..1) of the kind counts: the same tools in
  different amounts or not;
* ``order_distance`` – Levenshtein distance of the kind sequences over the longer length: the same
  steps in the same order or not;
* ``action_distance`` – the same over ``action`` sequences: the same files and commands or not;
* resource spread – coefficient of variation of tool calls, turns and output tokens.

**Strategies.** A task with several valid ways would read as unstable if every pair were compared.
Runs are grouped, single linkage, by the order distance of their kind sequences with consecutive
repeats collapsed (three ``Read`` in a row are one step), at ``threshold``. Each strategy reports its
runs, passes, inner distance and a representative path, so "two ways, both pass" is told apart from
"one way that breaks" and "one way that always fails". A cluster is a shape, not a proof that the
shape is right; the result and the contract judge that.

**Contract.** Optional rules over named actions, checked per run (process conformance): an action
must or must not happen, must come first, must follow the last of another, or must not fail at its
last call. The contract allows any order it does not constrain, so different correct paths pass.

Nothing here calls a model.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from dokimi.context import shell
from dokimi.context.trace import TraceError

_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
RULES = ("requires", "forbids", "before", "after_last", "last_ok")
_TARGET_KEYS = ("file_path", "notebook_path", "path", "pattern", "url", "query", "subagent_type")


@dataclass
class Event:
    tool: str
    kind: str
    action: str
    input: dict[str, Any]
    failed: bool = False
    masked: bool = False  # the call's status is another's: a pipe without pipefail, a later `;` / `||`, `&`, a loop
    call: int = -1  # the tool call this step came from; a chained command is one call and several steps


@dataclass
class Run:
    name: str
    events: list[Event] = field(default_factory=list)
    passed: bool | None = None
    num_turns: int | None = None
    output_tokens: int | None = None  # None: no message had a usage count
    calls_made: int = 0  # the next call's number, while a trace is read

    def calls(self) -> int:
        """Tool calls made: a chained command (``a && b``) is several steps but one call."""
        return len({e.call if e.call >= 0 else id(e) for e in self.events})

    def failed_calls(self) -> int:
        return len({e.call if e.call >= 0 else id(e) for e in self.events if e.failed})

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def actions(self) -> list[str]:
        return [e.action for e in self.events]

    def shape(self) -> list[str]:
        """The kind sequence with consecutive repeats collapsed: the strategy, not its length."""
        out: list[str] = []
        for k in self.kinds():
            if not out or out[-1] != k:
                out.append(k)
        return out


# --- reading -----------------------------------------------------------------------------------

def read_run(path: Path, grain: str = "split") -> Run:
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise TraceError(f"cannot read {path}: {exc}") from exc
    run = parse_run(raw, str(path), grain)
    if not run.events and run.num_turns is None:
        raise TraceError(f"{path}: no tool call and no result event; is it a "
                         "`claude -p --output-format stream-json --verbose` trace?")
    return run


def parse_run(raw: str, name: str = "", grain: str = "split") -> Run:
    """The main thread's steps in order, their failures, and the result event.

    ``grain="split"`` turns one ``Bash`` call into one step per command it chains (``a && b; c``),
    and a command that writes a file (``sed -i``, ``>``, ``tee``) carries that file as
    ``file_path``, so an edit made through the shell is an edit. ``grain="call"`` keeps one step
    per tool call. A Stop hook that blocked the stop is a failed ``Hook:Stop`` step; a hook that
    passed leaves nothing in the trace.

    A run that made tool calls but wrote no ``result`` event (it crashed, timed out, was cut off)
    did not finish: it counts as failed, so it cannot drop out of the pass rate.
    """
    run = Run(name)
    by_id: dict[str, list[Event]] = {}
    seen_messages: dict[str, int] = {}
    for line in raw.split(chr(10)):
        try:
            rec = json.loads(line) if line.strip() else None
        except (ValueError, RecursionError):
            continue
        if not isinstance(rec, dict):
            continue
        raw_message = rec.get("message")
        message: dict[str, Any] = {}
        if isinstance(raw_message, dict):
            message = raw_message
        raw_content = message.get("content")
        content: list[Any] = raw_content if isinstance(raw_content, list) else []
        main = rec.get("parent_tool_use_id") is None
        kind = rec.get("type")
        if kind == "assistant":
            _assistant(run, rec, message, content, main, by_id, seen_messages, grain)
        elif kind == "user":
            _user(run, content, main, by_id)
        elif kind == "result":
            run.num_turns = rec.get("num_turns") if isinstance(rec.get("num_turns"), int) else None
            run.passed = rec.get("subtype") == "success" and not rec.get("is_error")
    run.output_tokens = sum(seen_messages.values()) if seen_messages else None
    if run.passed is None and run.events:
        run.passed = False
    return run


def _assistant(run: Run, rec: dict[str, Any], message: dict[str, Any], content: list[Any], main: bool,
               by_id: dict[str, list[Event]], seen_messages: dict[str, int], grain: str) -> None:
    raw_usage = message.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    count = usage.get("output_tokens")
    if (isinstance(count, int) and not isinstance(count, bool)) or (isinstance(count, float) and math.isfinite(count)):
        mid = str(message.get("id") or id(rec))  # one call, many blocks
        seen_messages[mid] = max(seen_messages.get(mid, 0), int(count))
    if not main:
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            evs = _events(str(block.get("name") or ""), block.get("input"), grain)
            for e in evs:
                e.call = run.calls_made
            run.calls_made += 1
            run.events.extend(evs)
            by_id[str(block.get("id") or "")] = evs


def _user(run: Run, content: list[Any], main: bool, by_id: dict[str, list[Event]]) -> None:
    if main and any(_text(b).startswith("Stop hook feedback") for b in content):
        run.events.append(Event("Hook", "Hook:Stop", "Hook:Stop", {}, failed=True, call=run.calls_made))
        run.calls_made += 1
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
            # The trace does not say which chained command failed, so all of them may have:
            # a failed `make check && git diff` must not read as a passing `git diff`.
            for hit in by_id.get(str(block.get("tool_use_id") or ""), []):
                hit.failed = True


def run_from_events(events: list[dict[str, Any]], name: str = "") -> Run:
    """A run from the normalized ``events.jsonl`` of ``repo run`` (any harness), main thread only.

    A command is split as a ``Bash`` call is; a read is ``Read``, an edit ``Edit``, another tool its
    name. A command with a non-zero ``rc`` fails every command it chains, as a failed call does.
    """
    run = Run(name)
    for ev in events:
        if not isinstance(ev, dict) or ev.get("agent"):
            continue
        kind = ev.get("kind")
        if kind == "command":
            evs = _events("Bash", {"command": str(ev.get("text") or "")})
            for e in evs:
                e.failed = isinstance(ev.get("rc"), int) and ev["rc"] != 0
                e.call = len(run.events)
            run.events.extend(evs)
        elif kind in ("read", "edit"):
            tool = "Read" if kind == "read" else "Edit"
            path = str(ev.get("path") or "")
            run.events.append(Event(tool, tool, f"{tool}({Path(path).name})" if path else tool,
                                    {"file_path": path} if path else {}))
        elif kind == "tool" and ev.get("name"):
            tool = str(ev["name"])
            run.events.append(Event(tool, tool, tool, {}, failed=bool(ev.get("denied"))))
    return run


def order_distance(runs: list[Run]) -> float | None:
    """Mean order distance of the kind sequences over every pair of runs; ``None`` under two runs."""
    return _mean([seq_distance(a.kinds(), b.kinds()) for a, b in combinations(runs, 2)])


def _text(block: Any) -> str:
    return str(block.get("text") or "") if isinstance(block, dict) and block.get("type") == "text" else ""


def _events(tool: str, args: Any, grain: str = "split") -> list[Event]:
    args = args if isinstance(args, dict) else {}
    if tool == "Bash":
        return _bash_events(tool, args, grain)
    kind, target = tool, ""
    for key in _TARGET_KEYS:
        if args.get(key):
            value = str(args[key])
            target = Path(value).name if key.endswith("path") else value
            break
    return [Event(tool, kind, f"{kind}({target})" if target else kind, args)]


def _hides_status(sep: str, errexit: bool) -> bool:
    """Whether the call's status is a later command's, not that of the command before ``sep``;
    `set -e` stops a chain at its first failure, so after `;` or a newline it is still that command's."""
    return sep in shell.HIDING and not (errexit and sep in (";", "\n"))


def _opens_loop(seg: str) -> bool:
    """A loop header as a shell word (not in quotes or ``$(...)``): first, or after a pipe, ``(``,
    ``{``, ``!``, ``then``, ``do``, ``else``, ``elif``."""
    words = shell.tokens(seg)
    return any(w in ("for", "while", "until", "select")
               and (i == 0 or words[i - 1] in ("|", "(", "{", "!", "then", "do", "else", "elif"))
               for i, w in enumerate(words))


def _masks(parts: list[tuple[str, str]], command: str) -> list[bool]:
    """Per command of a line, whether the status the call reports may be another's: a pipe without
    `pipefail`; a later command after `||`, or after `;` or a newline (not under `set -e`); a
    trailing `&`, whose status is always 0; a loop body, whose status is the last iteration's."""
    pipefail = bool(re.search(r"\bpipefail\b", command))
    errexit = bool(re.search(r"\bset\s+(-[a-z]*e|-o\s+errexit)", command))
    out, loops = [], 0
    for i, (seg, sep) in enumerate(parts):
        loops += _opens_loop(seg)
        loops -= bool(loops and re.match(r"^done\b", seg))
        # `done`, `fi` after the last command close it: its status is the call's
        sep = sep if sep == "&" or any(shell.clean(later) for later, _ in parts[i + 1:]) else ""
        out.append((not pipefail and "|" in shell.tokens(seg)) or _hides_status(sep, errexit)
                   or (loops > 0 and not errexit))
    return out


def _bash_events(tool: str, args: dict[str, Any], grain: str) -> list[Event]:
    command = str(args.get("command") or "")
    parts = shell.split_commands(command)
    masks = _masks(parts, command)
    if grain == "split":
        out = []
        for (seg, _), masked in zip(parts, masks, strict=True):
            prog = shell.program(seg)
            if not prog:
                continue
            text = shell.step_text(seg)
            # one step per file a command writes (`sed -i s/a/b/ a.py b.py` edits two files)
            for written in shell.written_files(seg) or [""]:
                ev_input = {**args, "command": text, **({"file_path": written} if written else {})}
                target = Path(written).name if written else text
                out.append(Event(tool, f"Bash:{prog}", f"Bash:{prog}({target})", ev_input, masked=masked))
        if out:
            return out
    prog = shell.program(command)
    kind = f"Bash:{prog}" if prog else "Bash"
    masked = any(m for (seg, _), m in zip(parts, masks, strict=True) if shell.program(seg))
    return [Event(tool, kind, f"{kind}({command.strip()})" if command.strip() else kind, args, masked=masked)]


def load_outcomes(path: Path) -> dict[str, bool]:
    """``{"trace path or file name": true|false}``, or a list of ``{"trace": .., "passed": ..}``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        for r in data:
            if not isinstance(r, dict) or "trace" not in r or "passed" not in r:
                raise ValueError(f"{path}: each entry wants `trace` and `passed`, got {r!r}")
        data = {str(r["trace"]): r["passed"] for r in data}
    if not isinstance(data, dict) or not all(isinstance(v, bool) for v in data.values()):
        raise ValueError(f"{path}: want an object of trace -> true/false")
    return data


def apply_outcomes(runs: list[Run], outcomes: dict[str, bool]) -> list[str]:
    """Set each run's result from the verifier; returns the runs it had no verdict for.

    A key is a run's full path, or its file name or stem. A name or stem several runs share
    (``base/1.jsonl``, ``skill/1.jsonl``) is ambiguous and refused: give the full path.
    """
    names = [Path(r.name).name for r in runs]
    stems = [Path(r.name).stem for r in runs]
    missing = []
    for run, name, stem in zip(runs, names, stems, strict=True):
        if run.name in outcomes:
            run.passed = outcomes[run.name]
            continue
        for key, same in ((name, names), (stem, stems)):
            if key in outcomes:
                if same.count(key) > 1:
                    raise ValueError(f"outcome key {key!r} matches {same.count(key)} runs; "
                                     "use the full path of the trace")
                run.passed = outcomes[key]
                break
        else:
            run.passed = None
            missing.append(run.name)
    return missing


# --- contract ----------------------------------------------------------------------------------

@dataclass
class Matcher:
    tools: tuple[str, ...] = ()
    patterns: dict[str, re.Pattern[str]] = field(default_factory=dict)  # input key -> regex

    def __call__(self, ev: Event) -> bool:
        if self.tools and ev.tool not in self.tools and ev.kind not in self.tools:
            # a shell write (`sed -i`, `echo > f`, `| tee`) is an edit for a matcher of edit tools; a
            # program whose output is saved (`pytest > log`, `ls > f`) did not edit anything
            if not (ev.tool == "Bash" and ev.input.get("file_path") and set(self.tools) & _WRITE_TOOLS
                    and shell.writes_file(ev.kind.removeprefix("Bash:"))):
                return False
        return all(p.search(str(ev.input.get(k, ""))) for k, p in self.patterns.items())


def load_contract(path: Path) -> dict[str, Any]:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML ({exc})") from exc
    return parse_contract(data, str(path))


def _names(value: Any, where: str) -> tuple[str, ...]:
    """A string or a list of strings; anything else (a number, a nested list, a mapping) is refused."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ValueError(f"{where} wants a name or a list of names, got {value!r}")


def _pattern(value: Any, where: str) -> str:
    """A regex, or a list of them as one alternation; anything else (a number, a mapping) is refused,
    so that `[pytest, tox]` is not read as the character class `['pytest', 'tox']`."""
    if isinstance(value, list) and value:
        return "|".join(f"(?:{p})" for p in _names(value, where))
    if isinstance(value, str):
        return value
    raise ValueError(f"{where} wants a regex or a list of regexes, got {value!r}")


def parse_contract(data: Any, where: str = "contract") -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("actions"), dict):
        raise ValueError(f"{where}: want `actions:` (name -> matcher) and `rules:`")
    actions: dict[str, Matcher] = {}
    for name, spec in data["actions"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"{where}: action {name!r} is not a mapping")
        tools = _names(spec.get("tool"), f"{where}: action {name!r}: `tool`")
        try:
            patterns = {str(k): re.compile(_pattern(v, f"{where}: action {name!r}: `{k}`"))
                        for k, v in spec.items() if k != "tool"}
        except re.error as exc:
            raise ValueError(f"{where}: action {name!r}: bad regex ({exc})") from exc
        actions[str(name)] = Matcher(tools, patterns)
    rules = []
    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        raise ValueError(f"{where}: `rules:` wants a list")
    for i, rule in enumerate(raw_rules):
        if not isinstance(rule, dict) or len(rule) != 1 or next(iter(rule)) not in RULES:
            raise ValueError(f"{where}: rule {i} must be one of {', '.join(RULES)}")
        (kind, arg), = rule.items()
        names = list(_names(arg, f"{where}: rule {i} `{kind}`"))
        want = 2 if kind in ("before", "after_last") else 1
        if len(names) != want or any(n not in actions for n in names):
            raise ValueError(f"{where}: rule {i} `{kind}` wants {want} defined action name(s), got {arg!r}")
        rules.append((kind, names))
    return {"actions": actions, "rules": rules}


def check_contract(run: Run, contract: dict[str, Any]) -> list[str]:
    """The rules this run broke, each as ``kind: args``."""
    hits = {name: [i for i, ev in enumerate(run.events) if m(ev)] for name, m in contract["actions"].items()}
    broken = []
    for kind, names in contract["rules"]:
        a = hits[names[0]]
        b = hits[names[1]] if len(names) > 1 else []
        last = None
        if kind == "requires":
            ok = bool(a)
        elif kind == "forbids":
            ok = not a
        elif kind == "before":  # every b has an a earlier; vacuous with no b
            ok = not b or (bool(a) and a[0] < b[0])
        elif kind == "after_last":  # some b after the last a; vacuous with no a
            ok = not a or any(j > a[-1] for j in b)
        else:  # last_ok: the last a did not fail, and its status was not hidden (a pipe, `;`, `&`, a loop)
            last = run.events[a[-1]] if a else None
            ok = last is None or not (last.failed or last.masked)
        if not ok:
            masked = kind == "last_ok" and last is not None and not last.failed
            broken.append(f"{kind}: {' -> '.join(names)}" + (" (status hidden)" if masked else ""))
    return broken


# --- metrics -----------------------------------------------------------------------------------

def levenshtein(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def seq_distance(a: list[str], b: list[str]) -> float:
    longest = max(len(a), len(b))
    return levenshtein(a, b) / longest if longest else 0.0


def jsd(a: list[str], b: list[str]) -> float:
    """Jensen-Shannon divergence, base 2, of two bags of labels; 0 same mix, 1 disjoint."""
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    keys = set(a) | set(b)
    p = {k: a.count(k) / len(a) for k in keys}
    q = {k: b.count(k) / len(b) for k in keys}

    def kl(x: dict[str, float], m: dict[str, float]) -> float:
        return sum(x[k] * math.log2(x[k] / m[k]) for k in keys if x[k])

    m = {k: (p[k] + q[k]) / 2 for k in keys}
    return max(0.0, min(1.0, (kl(p, m) + kl(q, m)) / 2))


def pass_at(n: int, c: int, k: int) -> float:
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat(n: int, c: int, k: int) -> float:
    return math.comb(c, k) / math.comb(n, k)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _cv(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    if not mu:
        return 0.0
    sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))
    return sd / mu


def strategies(runs: list[Run], threshold: float) -> list[list[int]]:
    """Single-linkage groups of runs whose collapsed kind paths are within ``threshold``."""
    parent = list(range(len(runs)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    shapes = [r.shape() for r in runs]
    for i, j in combinations(range(len(runs)), 2):
        if seq_distance(shapes[i], shapes[j]) <= threshold:
            parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(runs)):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def _verdict(passes: int, judged: int) -> str:
    if not judged:
        return "unjudged"
    if judged == 1:  # one run says nothing about stability
        return "single run, passed" if passes else "single run, failed"
    if passes == judged:
        return "stable success"
    if passes == 0:
        return "stable failure"
    return "unstable"


def profile(runs: list[Run], contract: dict[str, Any] | None = None, threshold: float = 0.34,
            ks: tuple[int, ...] = (1, 2, 4, 8)) -> dict[str, Any]:
    """The three-axis profile of repeated runs of one task."""
    n = len(runs)
    judged = [r for r in runs if r.passed is not None]
    c = sum(1 for r in judged if r.passed)
    result: dict[str, Any] = {"runs": n, "judged": len(judged), "passed": c,
                              "success_rate": c / len(judged) if judged else None}
    for k in ks:
        if judged and k <= len(judged):
            result[f"pass@{k}"] = pass_at(len(judged), c, k)
            result[f"pass^{k}"] = pass_hat(len(judged), c, k)

    pairs = list(combinations(runs, 2))
    path = {
        "mix_jsd": _mean([jsd(a.kinds(), b.kinds()) for a, b in pairs]),
        "order_distance": _mean([seq_distance(a.kinds(), b.kinds()) for a, b in pairs]),
        "action_distance": _mean([seq_distance(a.actions(), b.actions()) for a, b in pairs]),
        "tool_calls_mean": _mean([r.calls() for r in runs]),
        "tool_calls_cv": _cv([r.calls() for r in runs]),
        "turns_cv": _cv([r.num_turns for r in runs if r.num_turns is not None]),
        "output_tokens_cv": _cv([r.output_tokens for r in runs if r.output_tokens is not None]),
        "failed_calls_mean": _mean([r.failed_calls() for r in runs]),
    }

    groups = []
    for members in strategies(runs, threshold):
        rs = [runs[i] for i in members]
        js = [r for r in rs if r.passed is not None]
        ps = sum(1 for r in js if r.passed)
        medoid = min(rs, key=lambda r: sum(seq_distance(r.shape(), o.shape()) for o in rs))
        groups.append({
            "runs": [r.name for r in rs], "passed": ps, "judged": len(js),
            "verdict": _verdict(ps, len(js)),
            # None for a one-run strategy: no pair, nothing measured
            "inner_distance": _mean([seq_distance(a.shape(), b.shape()) for a, b in combinations(rs, 2)]),
            "path": medoid.shape(),
        })

    out: dict[str, Any] = {"result": result, "path": path, "strategies": groups,
                           "verdict": _summary_verdict(groups)}
    if contract is not None:
        per_run = {r.name: check_contract(r, contract) for r in runs}
        tally: dict[str, int] = {}
        for broken in per_run.values():
            for rule in broken:
                tally[rule] = tally.get(rule, 0) + 1
        conforming = sum(1 for b in per_run.values() if not b)
        out["contract"] = {
            "rules": len(contract["rules"]), "conforming_runs": conforming,
            "conformance_rate": conforming / n if n else None,
            "violations": dict(sorted(tally.items(), key=lambda kv: -kv[1])),
            "per_run": per_run,
            # a pass that broke a rule: the result alone would have hidden it
            "passed_but_nonconforming": sum(1 for r in runs if r.passed and per_run[r.name]),
        }
    return out


def _summary_verdict(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return "no runs"
    verdicts = {g["verdict"] for g in groups}
    if len(groups) == 1:
        return f"one strategy, {groups[0]['verdict']}"
    if verdicts == {"stable success"}:  # a single passing run says nothing about stability
        return f"{len(groups)} strategies, all stable success (valid diversity)"
    return f"{len(groups)} strategies: " + ", ".join(
        f"{g['verdict']} ×{len(g['runs'])}" for g in groups)
