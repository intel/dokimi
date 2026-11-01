# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""L0 Python-fence check (contract §8.2): fences type-checked against an interpreter, or unknown without one."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos import parse_manifest
from dokimi.repos.l0 import Check, scan_repo
from dokimi.repos.source import WorkTree
from dokimi.repos.l0.typecheck import _fence_index

MANIFEST = {
    "schema": "repo_manifest.v1",
    "id": "toy",
    "source": {"url": "https://github.com/acme/toy"},
    "eval_dir": "dev/evals",
    "guidance": {"globs": ["**/AGENTS.md"]},
    "entry_points": {"grade": "true"},
    "tasks": "dev/evals/tasks",
    "build_systems": ["make"],
}

FENCE = "```"
AGENTS = f"""# Toy

{FENCE}python
import json

print(json.dumps({{"a": 1}}))
{FENCE}

{FENCE}python
import json

print(json.dumps(1).nosuch())
{FENCE}

{FENCE}python
f(<arg>)
{FENCE}
"""


def _tree(tmp_path: Path, agents: str = AGENTS) -> WorkTree:
    root = tmp_path / "r"
    root.mkdir()
    (root / "AGENTS.md").write_text(agents, encoding="utf-8")
    return WorkTree(root)


def _fence_claims(payload: dict) -> list[dict]:
    return [c for c in payload["claims"] if c["kind"] == "python_fence"]


def test_without_the_check_the_python_fences_are_not_claimed(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST))
    assert payload["typecheck"] is None
    assert _fence_claims(payload) == []


def test_mypy_against_an_interpreter_passes_the_good_fence_and_fails_the_stale_one(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST),
                        typecheck=Check("mypy", sys.executable))
    claims = {c["line"]: c for c in _fence_claims(payload)}
    assert claims[3]["status"] == "ok"  # the good fence opens on line 3
    stale = claims[12]  # the stale fence opens on line 9; its error is on code line 3, so the claim is line 12
    assert stale["status"] == "fail"
    assert "nosuch" in stale["why"]
    assert payload["typecheck"]["fail"] == 1
    assert payload["typecheck"]["checked"] == 2


def test_a_fence_that_is_not_python_is_unresolved_not_failed(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST),
                        typecheck=Check("mypy", sys.executable))
    placeholder = {c["line"]: c for c in _fence_claims(payload)}[15]  # opens on line 15
    assert placeholder["status"] == "unresolved"
    assert "not valid Python" in placeholder["why"]
    assert payload["typecheck"]["syntax"] == 1


def test_without_an_interpreter_every_checkable_fence_is_unknown(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST), typecheck=Check("mypy", None))
    fences = _fence_claims(payload)
    assert {c["status"] for c in fences} == {"unresolved"}
    assert all("unknown" in c["why"] for c in fences if "not valid" not in c["why"])
    assert payload["typecheck"] == {"tool": "mypy", "tool_version": None, "python": None, "fences": 3,
                                    "checked": 0, "fail": 0, "unknown": 2, "syntax": 1}


def test_the_fence_line_of_a_failure_is_the_guidance_line(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    failed = [c for c in _fence_claims(payload) if c["status"] == "fail"]
    assert [c["line"] for c in failed] == [12]


def test_the_report_names_the_tool_and_its_version(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    tc = payload["typecheck"]
    assert tc["tool"] == "mypy"
    assert tc["tool_version"] and tc["tool_version"][0].isdigit()


@pytest.mark.skipif(shutil.which("basedpyright") is None, reason="basedpyright is not installed")
def test_basedpyright_fails_the_same_stale_fence(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST),
                        typecheck=Check("basedpyright", sys.executable))
    failed = [c for c in _fence_claims(payload) if c["status"] == "fail"]
    assert [c["line"] for c in failed] == [12]


def test_fence_file_names_map_back_to_their_index_only():
    assert _fence_index("fence_3.py") == 3
    assert _fence_index("elsewhere/x/fence_12.py") == 12
    assert _fence_index("fence_x.py") is None
    assert _fence_index("pkg/other.py") is None


def test_the_scan_command_refuses_python_env_without_typecheck(tmp_path, capsys):
    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--python-env", sys.executable])
    from dokimi.commands.repo import _typecheck_or_exit

    with pytest.raises(SystemExit) as exc:
        _typecheck_or_exit(args)
    assert exc.value.code == 2
    assert "needs --typecheck" in capsys.readouterr().err


def test_the_scan_command_refuses_a_python_env_that_is_not_executable(tmp_path, capsys):
    missing = tmp_path / "no-such-python"
    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--typecheck", "--python-env", str(missing)])
    from dokimi.commands.repo import _typecheck_or_exit

    with pytest.raises(SystemExit) as exc:
        _typecheck_or_exit(args)
    assert exc.value.code == 2
    assert "not an executable interpreter" in capsys.readouterr().err


def test_bare_typecheck_flag_means_mypy(tmp_path):
    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--typecheck"])
    assert args.typecheck == "mypy"
    assert args.python_env is None


def test_the_payload_is_json_serialisable(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    json.dumps(payload)


def _scan_args(tmp_path: Path, *extra: str):
    """Parsed ``repo scan`` arguments for a plain tree whose AGENTS.md is the fixture."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "AGENTS.md").write_text(AGENTS, encoding="utf-8")
    manifest_file = tmp_path / "m.json"
    manifest_file.write_text(json.dumps(MANIFEST), encoding="utf-8")
    return build_parser().parse_args(["repo", "scan", str(tree), "--manifest-file", str(manifest_file), *extra])


def test_the_scan_text_report_names_the_tool_and_the_interpreter(tmp_path, capsys):
    args = _scan_args(tmp_path, "--typecheck", "--python-env", sys.executable)
    args.func(args)
    out = capsys.readouterr().out
    assert f"({sys.executable}): 3 python fences, 2 checked, 1 fail, 0 unknown, 1 not Python" in out
    assert "FAIL AGENTS.md:12 [python_fence]" in out


def test_the_scan_text_report_says_unknown_without_an_interpreter(tmp_path, capsys):
    args = _scan_args(tmp_path, "--typecheck")
    args.func(args)
    assert "(no --python-env: unknown)" in capsys.readouterr().out


def test_a_tool_failure_is_a_usage_error_not_a_traceback(tmp_path, capsys, monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    def broken(check, codes):
        raise l0_typecheck.TypecheckError("mypy exited 2: broken")

    monkeypatch.setattr(l0_typecheck, "_run", broken)
    args = _scan_args(tmp_path, "--typecheck", "--python-env", sys.executable)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "--typecheck mypy: mypy exited 2: broken" in capsys.readouterr().err


def test_basedpyright_missing_from_path_is_a_usage_error(tmp_path, capsys, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    args = _scan_args(tmp_path, "--typecheck", "basedpyright")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "--typecheck basedpyright: not on PATH" in capsys.readouterr().err
