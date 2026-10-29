# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Every command a printed hint tells the user to run names the `dokimi` console script and parses.

The hints are read by people who copy them, so each one is captured from the code that prints it,
the argv is taken from the text, and the argv goes through the same parse_args the CLI uses.
"""

import ast
import json
import re
import shlex
from pathlib import Path

import pytest

import dokimi
from dokimi import cli
from dokimi.commands import personas as personas_cmd

# A hint names its program, then a subcommand (lower case). `dokimi` is the console script from
# pyproject.toml [project.scripts]; `python cli.py` is the source-checkout shim, which hints must not name.
_HINT = re.compile(r"(?:^|\s)(?:python cli\.py|dokimi)\s+(?=[a-z])")


def _hinted_argvs(text: str) -> list[list[str]]:
    """The argv (program first) of each command in ``text``, with backslash continuations joined."""
    argvs = []
    for line in text.replace("\\\n", " ").splitlines():
        match = _HINT.search(line)
        if match:
            argvs.append(shlex.split(line[match.start():].lstrip()))
    return argvs


def _assert_hints_parse(text: str) -> None:
    argvs = _hinted_argvs(text)
    assert argvs, f"no hint found in:\n{text}"
    for argv in argvs:
        assert argv[0] == "dokimi", f"hint names {argv[0]!r}, not the console script: {' '.join(argv)}"
        cli.parse_args(argv[1:])  # SystemExit if the hint does not parse


class _FakeGenerator:
    def __init__(self, **_):
        pass

    def generate_personas(self, **_):
        return {"personas": [{"id": "p1", "name": "n", "skill_level": "novice"}]}

    def save_personas(self, personas, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(personas), encoding="utf-8")


def test_personas_discover_next_step_parses(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(personas_cmd, "PersonaGenerator", _FakeGenerator)
    args = cli.parse_args(["personas", "discover", "--product", "P", "--description", "d",
                           "--output", str(tmp_path / "P.json")])
    args.func(args)
    _assert_hints_parse(capsys.readouterr().out)


def test_personas_approve_next_step_parses(tmp_path, capsys):
    persona_file = tmp_path / "P.json"
    persona_file.write_text(json.dumps({"product": "P", "personas": [
        {"id": "p1", "name": "n", "skill_level": "novice"}]}), encoding="utf-8")
    args = cli.parse_args(["personas", "approve", "--file", str(persona_file)])
    args.func(args)
    _assert_hints_parse(capsys.readouterr().out)


def test_baseline_list_hint_parses(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no baselines/eval/manifest.json here
    args = cli.parse_args(["baseline", "list"])
    args.func(args)
    _assert_hints_parse(capsys.readouterr().out)


def test_baseline_compare_hint_parses(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    eval_file = tmp_path / "eval.json"
    eval_file.write_text("{}", encoding="utf-8")
    args = cli.parse_args(["baseline", "compare", "--eval", str(eval_file)])
    with pytest.raises(SystemExit):  # no baseline to compare against
        args.func(args)
    _assert_hints_parse(capsys.readouterr().out)


def test_baseline_only_report_hint_parses():
    from dokimi.report.model_compare import _BASELINE_ONLY_LIMITATION

    # The report quotes the command with a "> " prefix on every line.
    _assert_hints_parse("\n".join(line.removeprefix("> ") for line in _BASELINE_ONLY_LIMITATION))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of the string constants that are docstrings: prose, not text a user is shown or copies."""
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                nodes.add(id(body[0].value))
    return nodes


def test_no_string_in_the_package_names_the_source_shim():
    """Any string literal in the package that names `python cli.py` is a hint a user may copy; none may."""
    package = Path(dokimi.__file__).parent
    offenders = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings and "python cli.py" in node.value):
                offenders.append(f"{path.relative_to(package)}:{node.lineno}: {node.value.strip()}")
    assert not offenders, "hints name the source shim, not `dokimi`:\n" + "\n".join(offenders)
