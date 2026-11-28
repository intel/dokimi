# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The commands, flags, files and modules the docs name exist.

A doc that tells the reader to run a subcommand that was renamed, pass a flag that was
dropped, or open a file that moved is wrong in a way no reader can repair alone. The CLI
is the source of truth for commands and flags; the tree is the source of truth for paths.
Only code -- fenced blocks and inline code spans -- is checked, so prose that happens to
contain "dokimi" is not mistaken for a command.

Changelog fragments and the docstrings of public modules and CLI command functions are
checked too, for references whose kind is unambiguous: a command, a `--flag` right after
the command it belongs to, and (fragments) a repo path. A spelling the CLI retired and
still answers with its new one resolves there: a fragment records the change. Known docstring
misses are in ``docs_code_refs_baseline.txt``, which only shrinks; a fragment is fixed, never listed.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import functools
import importlib
import importlib.util
import inspect
import io
import re
import shlex
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from dokimi.cli import _repo_scan_compat, build_parser
from dokimi.cli_routes import TOMBSTONES, RetiredFlagError, moved_argv, moved_flags_argv

PROJECT = Path(__file__).resolve().parents[1]
DOCS = [
    *sorted((PROJECT / "docs").glob("*.md")),
    *sorted((PROJECT / "docs" / "user-journeys").glob("*.md")),
    # Bundled skills tell an agent which commands to run: a renamed one breaks the skill.
    *sorted((PROJECT / "data" / "skills").rglob("*.md")),
    PROJECT / "README.md",
    PROJECT / "CONTRIBUTING.md",
    PROJECT.parent / "README.md",
    PROJECT.parent / "AGENTS.md",
    PROJECT.parent / "REVIEW.md",
]
DOCS = [d for d in DOCS if d.exists()]

FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.M | re.S)
SPAN = re.compile(r"`([^`\n]+)`")
# `python cli.py ...`, `python -m dokimi ...`, `dokimi ...`
COMMAND = re.compile(
    r"(?:python3?\s+(?:-m\s+dokimi\b|\S*cli\.py)|(?<![\w/.=-])dokimi(?![\w/.-]))"
    r"\s+(.*)"
)
# Repo paths a reader is told to open. tests/ only as test modules: task-content docs
# talk about a task's own tests/ directory, which is not this repo's. The project
# directory and the package are both `dokimi/`, so a project doc's path resolves from
# the project directory; a top-level doc names repository paths and, in the sections
# about working inside the project, project paths, so either root resolves it.
PATH = re.compile(r"^(?:(?:dokimi|benchmarks|scripts|config)/[\w./-]+|tests/test_\w+\.py)$")
MODULE = re.compile(r"^dokimi(?:\.\w+)+$")
# `python -m dokimi.config_check ...`: the module a command runs.
RUN_MODULE = re.compile(r"python3?\s+-m\s+(dokimi(?:\.\w+)+)")
PLACEHOLDER = re.compile(r"[<>\[\]{}…*$]|\.\.\.")


def _code(text: str) -> list[str]:
    """Code lines: fenced blocks (continuations joined) and inline spans."""
    blocks = [re.sub(r"\s*\\\n\s*", " ", b) for b in FENCE.findall(text)]
    prose = FENCE.sub("", text)
    return [line for b in blocks for line in b.splitlines()] + SPAN.findall(prose)


def _subcommands(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    return {opt for action in parser._actions for opt in action.option_strings}


@functools.cache
def _root() -> argparse.ArgumentParser:
    return build_parser()


def _moved(tokens: list[str]) -> list[str] | None:
    """Where the CLI sends ``tokens`` (a retired command, or a group such as `eval score` with an old call)."""
    try:
        return moved_argv(tokens)
    except RetiredFlagError:
        return None


def _old_flag(argv: list[str]) -> bool:
    """True when ``argv`` names a flag spelling the CLI still answers: a move, or the `repo scan` shim."""
    if moved_flags_argv(argv) is not None:
        return True
    with contextlib.redirect_stderr(io.StringIO()):  # the shim warns on stderr
        return _repo_scan_compat(argv) != argv


def _walk(args: str, retired: bool) -> tuple[list[str] | None, list[str]]:
    """The command path ``args`` names (None if it names none, or a retired one) and its problems.

    With ``retired``, a command or flag the CLI moved (``cli_routes.TOMBSTONES``,
    ``cli_flags.FLAG_MOVES``) resolves: the CLI still answers it with the new spelling.
    """
    try:
        tokens = shlex.split(re.split(r"\s(?:\||&&|;|#|>)", args)[0])
    except ValueError:
        return None, []
    moved = _moved(tokens) if retired else None
    if moved is not None:
        # Validate the destination, so a flag the new command lacks is still reported.
        return _walk(shlex.join(moved), retired=True)
    parser, path, problems = _root(), [], []
    for token in tokens:
        subs = _subcommands(parser)
        if subs and not token.startswith("-"):
            if token in subs:
                parser = subs[token]
                path.append(token)
                continue
            if re.fullmatch(r"[a-z][a-z0-9-]*", token):
                problems.append(f"unknown command: {' '.join([*path, token])}")
            break
        if token.startswith("--") and not PLACEHOLDER.search(token):
            flag = token.split("=", 1)[0]
            if flag not in _flags(parser) and not (retired and _old_flag([*path, flag])):
                problems.append(f"unknown flag: {' '.join(path)} {flag}")
    return path or None, problems


def _check_command(args: str) -> list[str]:
    return _walk(args, retired=False)[1]


def _module_exists(dotted: str) -> bool:
    # The module itself, or an attribute of its parent module (`pkg.mod.func`). Resolved by
    # import: a file-existence test of the parent accepts any name under any package.
    try:
        if importlib.util.find_spec(dotted) is not None:
            return True
    except ImportError:
        pass
    parent, _, name = dotted.rpartition(".")
    try:
        return hasattr(importlib.import_module(parent), name)
    except ImportError:
        return False


def _problems(doc: Path) -> list[str]:
    found = []
    roots = [PROJECT] if PROJECT in doc.parents else [PROJECT.parent, PROJECT]
    for code in _code(doc.read_text(encoding="utf-8")):
        # A shell comment is prose: `make test  # runs the dokimi suite`.
        for match in COMMAND.finditer(re.split(r"(?:^|\s)#", code, maxsplit=1)[0]):
            found += _check_command(match.group(1))
        ref = code.strip()
        if PATH.match(ref) and not PLACEHOLDER.search(ref) and not any((r / ref).exists() for r in roots):
            found.append(f"missing path: {ref}")
        if MODULE.match(ref) and not _module_exists(ref):
            found.append(f"missing module: {ref}")
        for run in RUN_MODULE.findall(code):
            try:
                runnable = importlib.util.find_spec(run) is not None
            except ImportError:
                runnable = False
            if not runnable:
                found.append(f"missing module: {run}")
    return sorted(set(found))


FRAGMENTS = sorted((PROJECT / "changelog.d").glob("*.md"))
PACKAGE = PROJECT / "dokimi"
BASELINE = Path(__file__).with_name("docs_code_refs_baseline.txt")
LONG_FLAG = re.compile(r"--[a-z][a-z0-9-]*")
# A repo path a fragment names: a slash, a known extension, no `..`, not absolute.
FILE_REF = re.compile(r"[\w-][\w.-]*(?:/[\w.-]+)+\.(?:md|py|json|ya?ml|toml|txt|sh|j2)")
# Docs pages are lowercase; `docs/AGENTS.md` is the guidance file of a repository under test.
DOCS_PAGE = re.compile(r"docs/(?:[a-z0-9][\w/-]*|README)\.md")


def _command_roots() -> set[str]:
    return set(_subcommands(_root())) | {tomb.old[0] for tomb in TOMBSTONES}


def _subtree(path: list[str]) -> Iterator[tuple[list[str], argparse.ArgumentParser]]:
    """Each command at or under ``path``, with its parser (aliases once)."""
    parser = _root()
    for name in path:
        parser = _subcommands(parser)[name]
    todo, seen = [(path, parser)], set()
    while todo:
        here, parser = todo.pop()
        if id(parser) not in seen:
            seen.add(id(parser))
            yield here, parser
            todo += [([*here, name], sub) for name, sub in _subcommands(parser).items()]


@functools.cache
def _flag_of(path: tuple[str, ...], flag: str) -> bool:
    """True when ``flag`` is, or was (a retired spelling the CLI still answers), a flag at or under ``path``."""
    return any(flag in _flags(parser) or _old_flag([*here, flag]) for here, parser in _subtree(list(path)))


def _command(span: str) -> tuple[list[str] | None, list[str]]:
    """The command path a span names (`repo run`, `python cli.py repo run`) and its problems."""
    tokens = span.split()
    command = COMMAND.match(span)
    if command:
        return _walk(command.group(1), retired=True)
    if len(tokens) > 1 and tokens[0] in _command_roots() and span == span.strip():
        return _walk(span, retired=True)
    return None, []


def _span_argv(span: str) -> list[str]:
    """The tokens of the command a span names, for asking the CLI what it moved."""
    command = COMMAND.match(span)
    try:
        return shlex.split(command.group(1) if command else span)
    except ValueError:
        return []


def _consumed_by_move(argv: list[str], flag: str, written: list[str]) -> bool:
    """True when the CLI moved ``argv`` and its rewrite drops or renames ``flag`` (`explore list --json`).

    ``written`` is the flag span with its value (`--artifact foo`), which the rewrite needs. A rewrite
    that refuses the flag by itself (`--all-vs-all` needs its `--catalog`) still names a retired flag. A
    refusal that only comes from a value the flag does not take (`--no-such-flag foo`) does not.
    """
    try:
        return moved_argv(argv) is not None and flag not in {t.split("=", 1)[0] for t in moved_argv([*argv, *written]) or []}
    except RetiredFlagError:
        # A retired flag is refused by itself (`--all-vs-all`) or takes a plain value (`--artifact ''`).
        try:
            moved_argv([*argv, flag])
        except RetiredFlagError:
            return True
        try:
            moved_argv([*argv, flag, "x"])
        except RetiredFlagError:
            return False
        return True


def _unknown_flag(
    path: list[str] | None, flag: str | None, argv: list[str] | None = None, written: list[str] | None = None
) -> list[str]:
    if path is None or flag is None or _flag_of(tuple(path), flag) or (argv and _consumed_by_move(argv, flag, written or [flag])):
        return []
    return [f"unknown flag: {' '.join(path)} {flag}"]


# `--flag` on `cmd`: the other order a flag is tied to its command in. Not "of": `--atom-verdicts`
# of `skill novelty analyze` is the file that command writes.
FLAG_ON = re.compile(r"\s+on\s+")


def _span_problems(spans: list[tuple[str, str]]) -> list[str]:
    """Commands among ``spans`` (each with the text before it), and each `--flag` tied to one.

    A flag is tied to a command when only spaces part them (`tasks run` `--repeats`) or when
    written `--flag` on `cmd`. Any other flag span may be another tool's (`docker` `--init`).
    """
    found: list[str] = []
    context: list[str] | None = None
    argv: list[str] = []
    flag: str | None = None
    written: list[str] = []
    for span, gap in spans:
        head = span.split()[0] if span.split() else ""
        if head.startswith("--"):
            context = context if not gap.strip() else None
            flag = head.split("=", 1)[0] if LONG_FLAG.fullmatch(head.split("=", 1)[0]) else None
            written = _span_argv(span)
            found += _unknown_flag(context, flag, argv, written)
            continue
        context, problems = _command(span)
        argv = _span_argv(span)
        found += problems + (_unknown_flag(context, flag, argv, written) if FLAG_ON.fullmatch(gap) else [])
        flag = None
    return found


def _paragraphs(prose: str) -> list[str]:
    return re.split(r"\n\s*\n|\n(?=\s*[-*] )", prose)


def _spans(paragraph: str) -> list[tuple[str, str]]:
    """Each code span of ``paragraph``, with the text between it and the span before it."""
    found, end = [], 0
    for match in SPAN.finditer(paragraph):
        found.append((match.group(1), paragraph[end:match.start()]))
        end = match.end()
    return found


def _path_problem(ref: str) -> str | None:
    """``ref`` when it is a repo path that is missing while its directory is there."""
    if not FILE_REF.fullmatch(ref) or "/../" in f"/{ref}/" or PLACEHOLDER.search(ref):
        return None
    head = ref.split("/", 1)[0]
    if not (PATH.match(ref) or DOCS_PAGE.fullmatch(ref) or (PACKAGE / head).is_dir()):
        return None
    roots = [PROJECT, PROJECT.parent, PACKAGE]
    # A path whose directory is in no root is an example or another repository's path.
    if any((r / ref).exists() for r in roots) or not any((r / ref).parent.is_dir() for r in roots):
        return None
    return f"missing path: {ref}"


def _fragment_problems(text: str) -> list[str]:
    found = []
    for block in FENCE.findall(text):
        for line in re.sub(r"\s*\\\n\s*", " ", block).splitlines():
            for match in COMMAND.finditer(re.split(r"(?:^|\s)#", line, maxsplit=1)[0]):
                found += _walk(match.group(1), retired=True)[1]
    for paragraph in _paragraphs(FENCE.sub("", text)):
        spans = _spans(paragraph)
        found += _span_problems(spans)
        found += [p for p in (_path_problem(span) for span, _ in spans) if p]
    return found


def _docstring_problems(doc: str) -> list[str]:
    # reST spans (``--flag``) read as Markdown ones.
    return [p for par in _paragraphs(doc.replace("``", "`")) for p in _span_problems(_spans(par))]


def _rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT.parent).as_posix()


def _module_path(name: str) -> str:
    """The repository path of module ``name``, the same whether the package runs from the tree or is installed."""
    tail = "/__init__.py" if hasattr(sys.modules[name], "__path__") else ".py"
    return f"{PROJECT.name}/{name.replace('.', '/')}{tail}"


def _module_docstrings() -> Iterator[tuple[str, str]]:
    for file in sorted(PACKAGE.rglob("*.py")):
        parts = file.relative_to(PACKAGE).parts
        if any(p.startswith("_") and p != "__init__.py" for p in parts):
            continue
        doc = ast.get_docstring(ast.parse(file.read_text(encoding="utf-8")))
        if doc:
            yield _rel(file), doc


def _command_docstrings() -> Iterator[tuple[str, str]]:
    funcs = {id(f): f for _, p in _subtree([]) if (f := p.get_default("func")) is not None}
    for func in funcs.values():
        func = inspect.unwrap(getattr(func, "func", func))
        doc = inspect.getdoc(func)
        if doc:
            yield f"{_module_path(func.__module__)}::{func.__qualname__}", doc


def _all_problems() -> set[str]:
    found = {f"{_rel(f)}: {p}" for f in FRAGMENTS for p in _fragment_problems(f.read_text(encoding="utf-8"))}
    for label, doc in [*_module_docstrings(), *_command_docstrings()]:
        found |= {f"{label}: {p}" for p in _docstring_problems(doc)}
    return found


def _baseline(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def _drift(found: set[str], baseline: set[str]) -> dict[str, list[str]]:
    """Misses not in the baseline, and baseline entries that no longer miss (delete them)."""
    return {"new": sorted(found - baseline), "fixed, remove from the baseline": sorted(baseline - found)}


def test_fragment_and_docstring_references_resolve():
    baseline = _baseline(BASELINE.read_text(encoding="utf-8"))
    drift = _drift(_all_problems(), baseline)
    assert drift == {"new": [], "fixed, remove from the baseline": []}
    # The release deletes every fragment: a row naming one would then count as fixed and fail the release.
    assert not [row for row in baseline if "changelog.d/" in row], "fix a fragment, do not list it"


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: str(d.relative_to(PROJECT.parent)))
def test_doc_code_references_resolve(doc):
    assert _problems(doc) == []


def test_the_checker_sees_commands_flags_and_paths():
    assert _check_command("tasks run --tasks x --harness docker-oracle") == []
    assert _check_command("tasks sprint") == ["unknown command: tasks sprint"]
    assert _check_command("tasks run --no-such-flag") == ["unknown flag: tasks run --no-such-flag"]
    assert _check_command("tasks run --tasks <id> | tee log") == []
    assert _code("x `dokimi/cli.py` y\n```bash\npython cli.py tasks \\\n  run\n```\n") == [
        "python cli.py tasks run", "dokimi/cli.py"]


def test_the_checker_resolves_modules():
    assert _module_exists("dokimi.eval.stats.mean_ci")
    assert not _module_exists("dokimi.eval.nope")
    assert not _module_exists("dokimi.config_chek")


def test_the_checker_resolves_python_m_modules(tmp_path):
    doc = tmp_path / "x.md"
    doc.write_text("```bash\npython -m dokimi.config_chek --x\n"
                   "python -m dokimi.nope.deeper\npython -m dokimi.config_check\n```\n")
    assert _problems(doc) == ["missing module: dokimi.config_chek",
                              "missing module: dokimi.nope.deeper"]


def test_a_coverage_flag_naming_the_package_is_not_a_command(tmp_path):
    doc = tmp_path / "x.md"
    doc.write_text("```bash\npytest --cov=dokimi --cov-branch\n```\n")
    assert _problems(doc) == []


def test_paths_resolve_from_the_root_their_doc_is_written_against(tmp_path, monkeypatch):
    # The project directory and the package share a name: `dokimi/docs/x.md` is a repo
    # path in the top-level README and a (missing) package path in a project doc.
    project = tmp_path / "dokimi"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "x.md").write_text("")
    (project / "dokimi").mkdir()
    (project / "dokimi" / "cli.py").write_text("")
    monkeypatch.setattr(sys.modules[__name__], "PROJECT", project)
    refs = "`dokimi/docs/x.md` `dokimi/cli.py`\n"
    top, inner = tmp_path / "README.md", project / "docs" / "y.md"
    top.write_text(refs)
    inner.write_text(refs)
    assert _problems(top) == []
    assert _problems(inner) == ["missing path: dokimi/docs/x.md"]


# AGENTS.md: "Docs that name a command, flag, path or module must match the tree", for fragments too.
def test_a_fragment_flag_tied_to_its_command_must_exist():
    assert _fragment_problems("- `tasks run` `--no-such-flag`, and `--harness` on `tasks run`.\n") == [
        "unknown flag: tasks run --no-such-flag"]
    assert _fragment_problems("- `--no-such-flag` on `tasks run`.\n") == ["unknown flag: tasks run --no-such-flag"]
    assert _fragment_problems("- `tasks run` `--harness`, `tasks run --reps 2`.\n") == []


def test_a_fragment_command_and_path_must_exist():
    text = "- `tasks sprint` is described in `docs/no-such-page.md`, beside `docs/releasing.md`.\n"
    assert _fragment_problems(text) == ["unknown command: tasks sprint", "missing path: docs/no-such-page.md"]


def test_ambiguous_fragment_tokens_are_ignored():
    text = ("- `repo security` stops at the next `-v`, `--volume` or `--mount`; `docker` `--init`,\n"
            "  `docs/AGENTS.md`, `scripts/alpha/run.sh`, `runs` and `--no-such-flag` alone.\n")
    assert _fragment_problems(text) == []


def test_a_spelling_the_cli_retired_resolves_in_a_fragment_only():
    assert _fragment_problems("- `repo validate` moved; `tasks run --repeats 2`; `--jobs` on `repo run`.\n") == []
    assert _check_command("tasks run --repeats 2") == ["unknown flag: tasks run --repeats"]


def test_a_retired_command_still_needs_its_flags_to_exist():
    assert _fragment_problems("- `repo validate --schema-only` moved.\n") == []
    assert _fragment_problems("- `repo validate --no-such-flag` was run.\n") == ["unknown flag: eval check --no-such-flag"]
    assert _fragment_problems("- `repo validate` `--no-such-flag` was run.\n") == ["unknown flag: eval check --no-such-flag"]


def test_a_flag_the_retired_command_took_is_accepted_in_an_adjacent_span():
    assert _fragment_problems("- `explore list` `--json` was removed.\n") == []
    assert _fragment_problems("- `explore list --json` was removed.\n") == []
    assert _fragment_problems("- `explore list` `--no-such-flag` was removed.\n") == ["unknown flag: eval tasks list --no-such-flag"]


def test_an_unknown_flag_with_a_value_after_a_moved_command_is_reported():
    problem = ["unknown flag: skill routing --no-such-flag"]
    assert _fragment_problems("- `novelty routing` `--no-such-flag foo` was run.\n") == problem
    assert _fragment_problems("- `--no-such-flag foo` on `novelty routing`.\n") == problem


def test_a_retired_value_flag_whose_value_is_refused_is_still_a_known_flag():
    assert _fragment_problems("- `novelty routing` `--artifact ''` rejects an empty artifact.\n") == []
    assert _fragment_problems("- `novelty routing` `--catalog ''` rejects an empty catalog.\n") == []
    assert _fragment_problems("- `--artifact ''` on `novelty routing` is refused.\n") == []


def test_an_adjacent_retired_flag_keeps_its_value_and_never_crashes():
    assert _fragment_problems("- `novelty routing` `--artifact foo` moved.\n") == []
    assert _fragment_problems("- `novelty routing --artifact foo` moved.\n") == []
    assert _fragment_problems("- `novelty routing` `--all-vs-all` `--catalog skills` moved.\n") == []


def test_docstring_commands_and_tied_flags_must_exist():
    doc = "Run ``tasks run`` ``--no-such-flag``; ``docker`` ``--init`` and ``--harness`` on ``tasks run`` pass."
    assert _docstring_problems(doc) == ["unknown flag: tasks run --no-such-flag"]
    assert list(_command_docstrings())


def test_a_baseline_entry_that_no_longer_misses_fails():
    assert _baseline("# header\n\na: x\nb: y\n") == {"a: x", "b: y"}
    assert _drift({"a: x"}, {"a: x", "b: y"}) == {"new": [], "fixed, remove from the baseline": ["b: y"]}
    assert _drift({"a: x", "c: z"}, {"a: x"}) == {"new": ["c: z"], "fixed, remove from the baseline": []}


def test_a_group_the_cli_moved_resolves_with_its_old_flag_in_one_span():
    assert _fragment_problems("- `eval score --judge-placeholder` moved.\n") == []
    assert _fragment_problems("- `eval score --no-such-flag` moved.\n") == ["unknown flag: eval score pointwise --no-such-flag"]


def test_a_flag_with_a_value_after_a_retired_command_is_still_checked():
    assert _fragment_problems("- `repo validate` `--no-such-flag=foo` was run.\n") == ["unknown flag: eval check --no-such-flag"]
    assert _fragment_problems("- `--no-such-flag=foo` on `repo validate`.\n") == ["unknown flag: eval check --no-such-flag"]


def test_the_repo_scan_gate_flag_the_cli_still_accepts_resolves():
    assert _fragment_problems("- `--fail-on-fail` is now `--fail-on=fail` on `repo scan`.\n") == []
    assert _fragment_problems("- `repo scan --fail-on-fail` warns.\n") == []
