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
import shlex
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from dokimi.context.trace import TraceError

RULES = ("requires", "forbids", "before", "after_last", "last_ok")
_TARGET_KEYS = ("file_path", "notebook_path", "path", "pattern", "url", "query", "subagent_type")


@dataclass
class Event:
    tool: str
    kind: str
    action: str
    input: dict[str, Any]
    failed: bool = False
    masked: bool = False  # piped without pipefail: the status is the last stage's, not this command's


@dataclass
class Run:
    name: str
    events: list[Event] = field(default_factory=list)
    passed: bool | None = None
    num_turns: int | None = None
    output_tokens: int = 0

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

def read_run(path: Path, shell: str = "split") -> Run:
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise TraceError(f"cannot read {path}: {exc}") from exc
    run = parse_run(raw, str(path), shell)
    if not run.events and run.num_turns is None:
        raise TraceError(f"{path}: no tool call and no result event; is it a "
                         "`claude -p --output-format stream-json --verbose` trace?")
    return run


def parse_run(raw: str, name: str = "", shell: str = "split") -> Run:
    """The main thread's steps in order, their failures, and the result event.

    ``shell="split"`` turns one ``Bash`` call into one step per command it chains (``a && b; c``),
    and a command that writes a file (``sed -i``, ``>``, ``tee``) carries that file as
    ``file_path``, so an edit made through the shell is an edit. ``shell="call"`` keeps one step
    per tool call. A Stop hook that blocked the stop is a failed ``Hook:Stop`` step; a hook that
    passed leaves nothing in the trace.
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
        kind = rec.get("type")
        main = rec.get("parent_tool_use_id") is None
        raw_message = rec.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
        raw_content = message.get("content")
        content: list[Any] = raw_content if isinstance(raw_content, list) else []
        if kind == "assistant":
            raw_usage = message.get("usage")
            usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
            out = int(usage.get("output_tokens") or 0)
            mid = str(message.get("id") or id(rec))
            seen_messages[mid] = max(seen_messages.get(mid, 0), out)  # one call, many blocks
            if not main:
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    evs = _events(str(block.get("name") or ""), block.get("input"), shell)
                    run.events.extend(evs)
                    by_id[str(block.get("id") or "")] = evs
        elif kind == "user":
            if main and any(_text(b).startswith("Stop hook feedback") for b in content):
                run.events.append(Event("Hook", "Hook:Stop", "Hook:Stop", {}, failed=True))
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                    # The trace does not say which chained command failed, so all of them may have:
                    # a failed `make check && git diff` must not read as a passing `git diff`.
                    for hit in by_id.get(str(block.get("tool_use_id") or ""), []):
                        hit.failed = True
        elif kind == "result":
            run.num_turns = rec.get("num_turns") if isinstance(rec.get("num_turns"), int) else None
            run.passed = rec.get("subtype") == "success" and not rec.get("is_error")
    run.output_tokens = sum(seen_messages.values())
    return run


def _text(block: Any) -> str:
    return str(block.get("text") or "") if isinstance(block, dict) and block.get("type") == "text" else ""


def _events(tool: str, args: Any, shell: str = "split") -> list[Event]:
    args = args if isinstance(args, dict) else {}
    if tool == "Bash":
        command = str(args.get("command") or "")
        pipefail = bool(re.search(r"\bpipefail\b", command))
        segments = _segments(command)
        if shell == "split":
            out = []
            for seg in segments:
                program = _program(seg)
                if not program:
                    continue
                written = _written(seg)
                ev_input = {**args, "command": seg, **({"file_path": written} if written else {})}
                target = Path(written).name if written else seg.strip()
                out.append(Event(tool, f"Bash:{program}", f"Bash:{program}({target})", ev_input,
                                 masked=not pipefail and "|" in _tokens(seg)))
            if out:
                return out
        program = _program(command)
        kind = f"Bash:{program}" if program else "Bash"
        masked = not pipefail and any("|" in _tokens(seg) for seg in segments)
        return [Event(tool, kind, f"{kind}({command.strip()})" if command.strip() else kind, args, masked=masked)]
    kind, target = tool, ""
    for key in _TARGET_KEYS:
        if args.get(key):
            value = str(args[key])
            target = Path(value).name if key.endswith("path") else value
            break
    return [Event(tool, kind, f"{kind}({target})" if target else kind, args)]


def _segments(command: str) -> list[str]:
    """The commands a shell line chains (``&&``, ``||``, ``;``, newline outside quotes), heredoc
    bodies and comment lines dropped: their lines are data, not commands."""
    command = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\s*\1\s*(?=\n|$)", r"<<\1\2", command, flags=re.S)
    out, cur, quote, i = [], [], "", 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                cur.append(command[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "\\" and command[i + 1:i + 2] == "\n":  # a continued line
            i += 2
            continue
        elif ch in ";\n" or command[i:i + 2] in ("&&", "||"):
            out.append("".join(cur))
            cur = []
            i += 1 if ch in ";\n" else 2
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [seg.strip() for seg in out if seg.strip() and not seg.strip().startswith("#")]


def _tokens(segment: str) -> list[str]:
    """Shell words with unquoted operators (``|``, ``>``, ``>>``, ``2>&1``) as their own tokens, so a
    ``>`` or ``|`` inside quotes stays part of a word."""
    lex = shlex.shlex(segment, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return segment.split()


def _written(segment: str) -> str:
    """The file a shell command writes, if it is one of the plain ways to write one."""
    words = _tokens(segment)
    if words and Path(words[0]).name in ("sed", "perl") and any(re.match(r"^-\w*i", w) for w in words[1:]):
        files = [w for w in words[1:] if not w.startswith("-") and w not in _OPERATORS]
        if len(files) >= 2:
            return files[-1]
    for i, w in enumerate(words[:-1]):
        if w in (">", ">>", ">|") and not (i and words[i - 1].isdigit() and words[i - 1] != "1"):
            target = words[i + 1]
            if target not in _OPERATORS and target != "/dev/null":
                return target
        if w == "tee" and (i == 0 or words[i - 1] == "|"):
            rest = [x for x in words[i + 1:] if not x.startswith("-")]
            if rest and rest[0] not in _OPERATORS:
                return rest[0]
    return ""


_OPERATORS = {"|", "||", "&&", ";", "&", ">", ">>", ">|", ">&", "<", "<<", "&>", "&>>", "(", ")"}


def _program(command: str) -> str:
    """The program a shell line runs: past ``cd x &&``, ``VAR=1``, ``sudo``, ``uv run``/``python -m``."""
    segments = [s for s in re.split(r"&&|\|\||;|\n", command) if s.strip()]
    for seg in segments:
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        while words and (re.match(r"^\w+=", words[0]) or words[0] in ("sudo", "env", "time", "exec")):
            words = words[1:]
        if words and words[:2] == ["uv", "run"]:
            words = words[2:]
        if len(words) >= 3 and re.match(r"^python[\d.]*$", Path(words[0]).name) and words[1] == "-m":
            words = words[2:]
        if not words or words[0] in ("cd", "pushd", "source", ".", "export", "set"):
            continue
        return Path(words[0]).name
    return ""


def load_outcomes(path: Path) -> dict[str, bool]:
    """``{"trace path or file name": true|false}``, or a list of ``{"trace": .., "passed": ..}``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {str(r["trace"]): r["passed"] for r in data if isinstance(r, dict) and "trace" in r}
    if not isinstance(data, dict) or not all(isinstance(v, bool) for v in data.values()):
        raise ValueError(f"{path}: want an object of trace -> true/false")
    return data


def apply_outcomes(runs: list[Run], outcomes: dict[str, bool]) -> list[str]:
    """Set each run's result from the verifier; returns the runs it had no verdict for."""
    missing = []
    for run in runs:
        for key in (run.name, Path(run.name).name, Path(run.name).stem, Path(run.name).parent.name):
            if key in outcomes:
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
            return False
        return all(p.search(str(ev.input.get(k, ""))) for k, p in self.patterns.items())


def load_contract(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_contract(data, str(path))


def parse_contract(data: Any, where: str = "contract") -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("actions"), dict):
        raise ValueError(f"{where}: want `actions:` (name -> matcher) and `rules:`")
    actions: dict[str, Matcher] = {}
    for name, spec in data["actions"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"{where}: action {name!r} is not a mapping")
        tools = spec.get("tool") or ()
        tools = (tools,) if isinstance(tools, str) else tuple(str(t) for t in tools)
        try:
            patterns = {str(k): re.compile(str(v)) for k, v in spec.items() if k != "tool"}
        except re.error as exc:
            raise ValueError(f"{where}: action {name!r}: bad regex ({exc})") from exc
        actions[str(name)] = Matcher(tools, patterns)
    rules = []
    for i, rule in enumerate(data.get("rules") or []):
        if not isinstance(rule, dict) or len(rule) != 1 or next(iter(rule)) not in RULES:
            raise ValueError(f"{where}: rule {i} must be one of {', '.join(RULES)}")
        (kind, arg), = rule.items()
        names = [arg] if isinstance(arg, str) else list(arg or [])
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
        else:  # last_ok: the last a did not fail, and its status was not hidden by a pipe
            last = run.events[a[-1]] if a else None
            ok = last is None or not (last.failed or last.masked)
        if not ok:
            masked = kind == "last_ok" and last is not None and not last.failed
            broken.append(f"{kind}: {' -> '.join(names)}" + (" (status hidden by a pipe)" if masked else ""))
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
        "tool_calls_mean": _mean([len(r.events) for r in runs]),
        "tool_calls_cv": _cv([len(r.events) for r in runs]),
        "turns_cv": _cv([r.num_turns for r in runs if r.num_turns is not None]),
        "output_tokens_cv": _cv([r.output_tokens for r in runs]),
        "failed_calls_mean": _mean([sum(e.failed for e in r.events) for r in runs]),
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
            "inner_distance": _mean([seq_distance(a.shape(), b.shape()) for a, b in combinations(rs, 2)]) or 0.0,
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
    verdicts = {g["verdict"] for g in groups}
    if len(groups) == 1:
        return f"one strategy, {groups[0]['verdict']}"
    if verdicts <= {"stable success", "single run, passed"}:
        return f"{len(groups)} strategies, all stable success (valid diversity)"
    return f"{len(groups)} strategies: " + ", ".join(
        f"{g['verdict']} ×{len(g['runs'])}" for g in groups)
