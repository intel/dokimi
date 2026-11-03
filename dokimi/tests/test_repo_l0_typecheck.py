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
    assert "not parsed by" in placeholder["why"] and "invalid syntax" in placeholder["why"]
    assert payload["typecheck"]["syntax"] == 1


def test_without_an_interpreter_every_checkable_fence_is_unknown(tmp_path):
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST), typecheck=Check("mypy", None))
    fences = _fence_claims(payload)
    assert {c["status"] for c in fences} == {"unresolved"}
    assert all("unknown" in c["why"] for c in fences if "not parsed by" not in c["why"])
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
    assert _fence_index("fence-3.py") == 3
    assert _fence_index("elsewhere/x/fence-12.py") == 12
    assert _fence_index("fence-x.py") is None
    assert _fence_index("fence_3.py") is None  # the name an import could reach: not a fence file
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
    from dokimi.commands.repo import _typecheck_or_exit

    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--typecheck"])
    assert args.typecheck is True
    assert args.python_env is None
    assert _typecheck_or_exit(args).tool == "mypy"


def test_the_typecheck_flag_takes_no_value_so_a_source_after_it_is_not_its_checker(tmp_path):
    # `repo scan --typecheck <source>`: the source must not be read as the checker name
    args = build_parser().parse_args(["repo", "scan", "--typecheck", str(tmp_path)])
    assert str(tmp_path) in vars(args).values()
    assert args.typecheck is True


def test_the_checker_is_chosen_by_typecheck_tool(tmp_path):
    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--typecheck", "--typecheck-tool", "basedpyright"])
    assert args.typecheck_tool == "basedpyright"


def test_typecheck_tool_without_typecheck_is_refused(tmp_path, capsys):
    from dokimi.commands.repo import _typecheck_or_exit

    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--typecheck-tool", "mypy"])
    with pytest.raises(SystemExit) as exc:
        _typecheck_or_exit(args)
    assert exc.value.code == 2
    assert "needs --typecheck" in capsys.readouterr().err


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

    def broken(check, codes, target=None):
        raise l0_typecheck.TypecheckError("mypy exited 2: broken")

    monkeypatch.setattr(l0_typecheck, "_run", broken)
    args = _scan_args(tmp_path, "--typecheck", "--python-env", sys.executable)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "repo scan --typecheck: mypy exited 2: broken" in capsys.readouterr().err


def test_an_interpreter_that_reports_no_version_is_not_labelled_as_the_tool(tmp_path, capsys, monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    def no_version(check):
        raise l0_typecheck.TypecheckError("/venv/bin/python did not report its version: ''")

    monkeypatch.setattr(l0_typecheck, "python_version", no_version)
    args = _scan_args(tmp_path, "--typecheck", "--python-env", sys.executable)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "did not report its version" in err and "--typecheck-tool" not in err


def test_basedpyright_missing_from_path_is_a_usage_error(tmp_path, capsys, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    args = _scan_args(tmp_path, "--typecheck", "--typecheck-tool", "basedpyright")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "--typecheck-tool basedpyright: not on PATH" in capsys.readouterr().err


def test_an_interpreter_with_nothing_to_check_runs_no_tool(tmp_path):
    payload = scan_repo(_tree(tmp_path, "# Toy\n\nNo code here.\n"), parse_manifest(MANIFEST),
                        typecheck=Check("mypy", sys.executable))
    assert payload["typecheck"]["checked"] == 0
    assert payload["typecheck"]["fail"] == 0


def _completed(**over):
    from types import SimpleNamespace

    done = {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False, "truncated": False}
    done.update(over)
    return SimpleNamespace(**done)


def test_a_tool_that_exits_one_with_no_report_is_an_error_not_a_pass(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    monkeypatch.setattr(l0_typecheck, "_run_tool", lambda argv, cwd: _completed(
        returncode=1, stderr="No module named mypy"))
    with pytest.raises(l0_typecheck.TypecheckError, match="no report"):
        l0_typecheck._run(Check("mypy", sys.executable), ["x: int = 1\n"])


def test_truncated_tool_output_is_an_error_not_a_partial_result(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    monkeypatch.setattr(l0_typecheck, "_run_tool", lambda argv, cwd: _completed(truncated=True))
    with pytest.raises(l0_typecheck.TypecheckError, match="truncated"):
        l0_typecheck._run(Check("mypy", sys.executable), ["x: int = 1\n"])


def test_a_relative_python_env_is_made_absolute_before_the_tool_runs(tmp_path, monkeypatch):
    from dokimi.commands.repo import _typecheck_or_exit

    link = tmp_path / "py"
    link.symlink_to(sys.executable)
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--typecheck", "--python-env", "py"])
    assert Path(_typecheck_or_exit(args).python).is_absolute()


def test_a_fence_too_deep_for_the_parser_is_unresolved_not_a_crash(tmp_path):
    deep = "x = " + "+".join(["1"] * 200000)  # ast.parse raises RecursionError on this (CPython 3.14 among them)
    agents = f"# Toy\n\n{FENCE}python\n{deep}\n{FENCE}\n"
    payload = scan_repo(_tree(tmp_path, agents), parse_manifest(MANIFEST), typecheck=Check("mypy", None))
    [claim] = _fence_claims(payload)
    assert claim["status"] == "unresolved"
    assert "RecursionError" in claim["why"]
    assert payload["typecheck"]["syntax"] == 1


def test_a_line_less_mypy_error_is_the_fence_line_not_the_line_above(tmp_path):
    from dokimi.repos.l0.typecheck import _errors

    report = json.dumps({"file": "fence-0.py", "line": -1, "severity": "error", "message": "no line"})
    assert _errors("mypy", report) == {0: [(0, "no line")]}


def test_mypy_checks_untyped_function_bodies(tmp_path):
    # Guidance examples are often unannotated functions; mypy skips their bodies unless told not to.
    agents = f"{FENCE}python\nimport json\n\ndef example():\n    json.dumps(1).nosuch()\n{FENCE}\n"
    payload = scan_repo(_tree(tmp_path, agents), parse_manifest(MANIFEST),
                        typecheck=Check("mypy", sys.executable))
    [claim] = _fence_claims(payload)
    assert claim["status"] == "fail"
    assert "nosuch" in claim["why"]


def test_a_subject_or_host_mypy_config_cannot_suppress_a_fence(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "mypy").mkdir(parents=True)
    (home / ".config" / "mypy" / "config").write_text("[mypy]\nignore_errors = True\n", encoding="utf-8")
    (home / ".mypy.ini").write_text("[mypy]\nignore_errors = True\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    tree = _tree(tmp_path, f"{FENCE}python\nimport json\nprint(json.dumps(1).nosuch())\n{FENCE}\n")
    (tree.root / "mypy.ini").write_text("[mypy]\nignore_errors = True\n", encoding="utf-8")
    [claim] = _fence_claims(scan_repo(tree, parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable)))
    assert claim["status"] == "fail"


def test_a_host_mypypath_cannot_supply_a_module_the_pinned_environment_lacks(tmp_path, monkeypatch):
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "ghost_pkg_mod.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("MYPYPATH", str(extra))
    tree = _tree(tmp_path, f"{FENCE}python\nimport ghost_pkg_mod\nprint(ghost_pkg_mod.run())\n{FENCE}\n")
    [claim] = _fence_claims(scan_repo(tree, parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable)))
    assert claim["status"] == "fail"


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in interpreter is a POSIX shell script")
def test_a_python_env_that_reports_no_version_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    silent = tmp_path / "silent-python"
    silent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    silent.chmod(0o755)
    args = _scan_args(tmp_path, "--typecheck", "--python-env", str(silent))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "did not report its version" in capsys.readouterr().err


def test_the_version_probe_is_not_shadowed_by_a_module_in_the_checkout(tmp_path, monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    import mypy.version

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "mypy.py").write_text("print('mypy 9.9.9')\n", encoding="utf-8")
    monkeypatch.chdir(checkout)
    assert l0_typecheck.tool_version(Check("mypy", sys.executable)) == mypy.version.__version__


def test_mypy_is_told_the_repository_interpreters_version(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    seen = []

    def fake(argv, cwd):
        seen.append(argv)
        return _completed(returncode=0, stdout="")

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    l0_typecheck._run(Check("mypy", sys.executable), ["x.py"], (3, 11))
    argv = seen[0]
    assert argv[argv.index("--python-version") + 1] == "3.11"


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="the fence needs a repository interpreter older than 3.12")
def test_a_fence_newer_than_the_repository_grammar_is_a_syntax_error(tmp_path):
    # `type` is a 3.12 statement: the repository's interpreter (here, 3.11) is what parses the fence
    tree = _tree(tmp_path, f"{FENCE}python\ntype Alias = int\n{FENCE}\n")
    payload = scan_repo(tree, parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    assert payload["typecheck"]["syntax"] == 1


def _fake_tools(monkeypatch, mypy_result, seen: list):
    """Stand-in for the tool runs: the version probes answer, the parse passes every fence, mypy gives ``mypy_result``."""
    from dokimi.repos.l0 import typecheck as l0_typecheck

    def fake(argv, cwd, stdin=None):
        seen.append(argv)
        if "ast" in argv[-1]:  # the fence parse, run by the repository's interpreter
            return _completed(returncode=0, stdout=json.dumps([None] * len(json.loads(stdin))))
        if "version_info" in argv[-1]:
            return _completed(returncode=0, stdout="3 11\n")
        if "--version" in argv:
            return _completed(returncode=0, stdout="mypy 2.4.0 (compiled: no)\n")
        return mypy_result

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)


def test_a_tool_that_stops_on_a_blocking_error_is_run_again_on_the_fences_it_did_not_name(tmp_path, monkeypatch):
    blocking = json.dumps({"file": "fence-0.py", "line": 1, "severity": "error", "message": "blocking"})
    stale = json.dumps({"file": "fence-0.py", "line": 1, "severity": "error", "message": "stale"})
    runs: list = []
    _fake_tools(monkeypatch, None, runs)
    from dokimi.repos.l0 import typecheck as l0_typecheck

    fake = l0_typecheck._run_tool

    def per_run(argv, cwd, stdin=None):
        done = fake(argv, cwd, stdin)
        if done is not None:
            return done
        mypy_runs = [a for a in runs if "--output" in a]
        report = blocking if len(mypy_runs) == 1 else stale  # the second run has only the other fence, as fence-0
        return _completed(returncode=2 if len(mypy_runs) == 1 else 1, stdout=report + "\n")

    monkeypatch.setattr(l0_typecheck, "_run_tool", per_run)
    agents = f"{FENCE}python\nx: int = 1\n{FENCE}\n\n{FENCE}python\ny: int = 2\n{FENCE}\n"
    payload = scan_repo(_tree(tmp_path, agents), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    claims = _fence_claims(payload)
    assert [c["status"] for c in claims] == ["fail", "fail"]
    assert "blocking" in claims[0]["why"] and "stale" in claims[1]["why"]
    assert [a[-1] for a in runs if "--output" in a] == ["fence-1.py", "fence-0.py"]
    assert payload["typecheck"]["checked"] == 2


def test_a_blocking_fragment_fence_does_not_hide_a_stale_fence(tmp_path):
    # a top-level yield stops mypy and is itself a fragment: the stale fence must still be named, not unresolved
    agents = (f"{FENCE}python\nconn = open('/dev/null')\nyield conn\n{FENCE}\n\n"
              f"{FENCE}python\nimport json\njson.dumps(1).nosuch()\n{FENCE}\n")
    payload = scan_repo(_tree(tmp_path, agents), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    claims = _fence_claims(payload)
    assert [c["status"] for c in claims] == ["unresolved", "fail"]
    assert "nosuch" in claims[1]["why"]
    assert payload["typecheck"]["fail"] == 1


def test_a_tool_that_exits_two_with_no_fence_report_is_an_error_not_a_pass(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    monkeypatch.setattr(l0_typecheck, "_run_tool", lambda argv, cwd, stdin=None: _completed(
        returncode=2, stderr="usage: mypy [-h]"))
    with pytest.raises(l0_typecheck.TypecheckError, match="exited 2 with no fence report"):
        l0_typecheck._run(Check("mypy", sys.executable), ["x: int = 1\n"])


def test_basedpyright_is_told_the_repository_interpreters_version(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    seen = {}

    def fake(argv, cwd, stdin=None):
        seen["config"] = json.loads(Path(argv[argv.index("--project") + 1]).read_text(encoding="utf-8"))
        return _completed(returncode=0, stdout='{"generalDiagnostics": []}')

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    l0_typecheck._run(Check("basedpyright", sys.executable), ["x: int = 1\n"], (3, 11))
    assert seen["config"] == {"pythonVersion": "3.11"}  # not the interpreter's own version: the target


def test_basedpyright_is_given_an_empty_project_so_no_config_above_the_run_applies(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    seen = {}

    def fake(argv, cwd, stdin=None):
        seen["argv"] = argv
        seen["config"] = Path(argv[argv.index("--project") + 1]).read_text(encoding="utf-8")  # while it exists
        return _completed(returncode=0, stdout='{"generalDiagnostics": []}')  # basedpyright always writes its report

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    l0_typecheck._run(Check("basedpyright", sys.executable), ["x: int = 1\n"])
    assert seen["argv"][0] == "basedpyright"
    assert seen["config"] == "{}\n"


def test_a_fence_is_parsed_by_the_repository_interpreter_not_the_host(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    seen = []

    def fake(argv, cwd, stdin=None):
        seen.append(argv)
        return _completed(returncode=0, stdout=json.dumps([None]))

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    assert l0_typecheck._syntax_errors(Check("mypy", "/opt/repo/python"), ["x = 1\n"]) == [None]
    assert seen[0][:3] == ["/opt/repo/python", "-I", "-c"]


@pytest.mark.skipif(not Path("/bin/true").exists(), reason="needs /bin/true")
def test_a_true_python_env_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    # /bin/true exits 0 with no output: it reports no version, so the scan refuses it cleanly
    args = _scan_args(tmp_path, "--typecheck", "--python-env", "/bin/true")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "did not report its version" in capsys.readouterr().err


def test_a_blocking_type_comment_is_a_failing_fence_not_a_tool_failure(tmp_path):
    # ast.parse reads no type comments by default, so this passes the parse; mypy then exits 2 on it
    tree = _tree(tmp_path, f"{FENCE}python\nx = 1  # type: ???\n{FENCE}\n")
    [claim] = _fence_claims(scan_repo(tree, parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable)))
    assert claim["status"] == "fail"
    assert "type comment" in claim["why"]


def test_a_linked_guidance_file_has_its_fences_checked_and_counted_once(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "AGENTS.md").write_text(f"{FENCE}python\nimport json\n\njson.nosuch()\n{FENCE}\n", encoding="utf-8")
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    manifest = parse_manifest(dict(MANIFEST, guidance={"globs": ["AGENTS.md", "CLAUDE.md"]}))
    payload = scan_repo(WorkTree(root), manifest, typecheck=Check("mypy", sys.executable))
    assert len(_fence_claims(payload)) == 1
    assert payload["typecheck"]["fences"] == 1


def test_only_python_tagged_fences_become_claims(tmp_path):
    bad = "import json\n\njson.nosuch()\n"
    agents = f"{FENCE}python\n{bad}{FENCE}\n\n{FENCE}bash\n{bad}{FENCE}\n\n{FENCE}json\n{bad}{FENCE}\n"
    payload = scan_repo(_tree(tmp_path, agents), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    assert payload["typecheck"]["fences"] == 1
    assert [c["status"] for c in _fence_claims(payload)] == ["fail"]


def test_a_fence_cannot_import_another_fence_as_a_module(tmp_path):
    agents = f"{FENCE}python\nvalue: int = 1\n{FENCE}\n\n{FENCE}python\nimport fence_0\nprint(fence_0.value)\n{FENCE}\n"
    payload = scan_repo(_tree(tmp_path, agents), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    assert [c["status"] for c in _fence_claims(payload)] == ["ok", "fail"]


def test_mypy_is_given_the_repository_interpreter(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    seen = []

    def fake(argv, cwd, stdin=None):
        seen.append(argv)
        return _completed(returncode=0, stdout="")

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    l0_typecheck._run(Check("mypy", "/opt/repo/bin/python"), ["x: int = 1\n"])
    argv = seen[0]
    assert argv[argv.index("--python-executable") + 1] == "/opt/repo/bin/python"


def test_nothing_to_check_runs_no_tool_not_even_a_version_probe(tmp_path, monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    seen = []

    def fake(argv, cwd, stdin=None):
        seen.append(argv)
        return _completed(returncode=1, stderr="No module named mypy")

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    payload = scan_repo(_tree(tmp_path, "# Toy\n\nNo code here.\n"), parse_manifest(MANIFEST),
                        typecheck=Check("mypy", sys.executable))
    assert seen == []
    assert payload["typecheck"]["tool_version"] is None


def test_fences_that_do_not_parse_run_no_tool_either(tmp_path, monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    seen = []

    def fake(argv, cwd, stdin=None):
        seen.append(argv)
        if "version_info" in argv[-1]:
            return _completed(returncode=0, stdout="3 11\n")
        return _completed(returncode=0, stdout=json.dumps(["invalid syntax"]))  # the parse: one fence, not valid

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    payload = scan_repo(_tree(tmp_path, f"{FENCE}python\nx = (\n{FENCE}\n"), parse_manifest(MANIFEST),
                        typecheck=Check("mypy", sys.executable))
    assert all("--version" not in argv and "mypy" not in argv for argv in seen)  # no tool run
    assert payload["typecheck"]["syntax"] == 1
    assert payload["typecheck"]["tool_version"] is None


def test_basedpyright_exit_two_is_the_tools_own_failure(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    monkeypatch.setattr(l0_typecheck, "_run_tool", lambda argv, cwd, stdin=None: _completed(
        returncode=2, stdout="{}", stderr="fatal"))
    with pytest.raises(l0_typecheck.TypecheckError, match="exited 2"):
        l0_typecheck._run(Check("basedpyright", sys.executable), ["x: int = 1\n"])


def test_an_exit_code_outside_zero_to_two_is_the_tools_own_failure(monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    monkeypatch.setattr(l0_typecheck, "_run_tool", lambda argv, cwd, stdin=None: _completed(
        returncode=3, stdout="", stderr="crashed"))
    with pytest.raises(l0_typecheck.TypecheckError, match="exited 3"):
        l0_typecheck._run(Check("mypy", sys.executable), ["x: int = 1\n"])


def test_only_error_diagnostics_count_as_fence_errors(tmp_path):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    mypy_out = "\n".join(json.dumps({"file": "fence-0.py", "line": n, "severity": sev, "message": "m"})
                         for n, sev in [(1, "error"), (2, "note"), (3, "warning")])
    assert l0_typecheck._errors("mypy", mypy_out) == {0: [(1, "m")]}
    pyright_out = json.dumps({"generalDiagnostics": [
        {"file": str(tmp_path / "fence-0.py"), "severity": sev, "message": sev, "range": {"start": {"line": n}}}
        for n, sev in [(0, "error"), (1, "warning"), (2, "information")]]})
    assert l0_typecheck._errors("basedpyright", pyright_out) == {0: [(1, "error")]}


@pytest.mark.parametrize("tool,stdout", [
    ("mypy", "not JSON"),
    ("mypy", "[1, 2]"),
    ("basedpyright", "not JSON"),
    ("basedpyright", '{"generalDiagnostics": [{"severity": "error", "message": "m"}]}'),  # no range
    ("mypy", "{}"),  # valid JSON, no diagnostic fields: not a clean run
    ("mypy", '{"file": "fence-0.py", "line": 1, "message": "m"}'),  # no severity
    ("basedpyright", "{}"),  # no generalDiagnostics list
    ("basedpyright", '{"generalDiagnostics": 3}'),
])
def test_a_nonempty_report_that_is_not_the_tools_json_is_a_tool_failure(monkeypatch, tool, stdout):
    # Exit 1 says the tool found errors; a report we cannot read must not pass every fence as clean.
    from dokimi.repos.l0 import typecheck as l0_typecheck

    monkeypatch.setattr(l0_typecheck, "_run_tool", lambda argv, cwd, stdin=None: _completed(
        returncode=1, stdout=stdout, stderr=""))
    with pytest.raises(l0_typecheck.TypecheckError, match="report"):
        l0_typecheck._run(Check(tool, sys.executable), ["x: int = 1\n"])


def test_a_repository_older_than_mypy_supports_is_unresolved_and_mypy_is_not_run(tmp_path, monkeypatch):
    # mypy 2.x refuses --python-version 3.9 and exits 2: the fences are unknown for that reason, not a tool failure
    from dokimi.repos.l0 import typecheck as l0_typecheck

    monkeypatch.setattr(l0_typecheck, "python_version", lambda check: (3, 9))

    def no_run(*args, **kwargs):
        raise AssertionError("mypy must not run for a target it refuses")

    monkeypatch.setattr(l0_typecheck, "_run", no_run)
    payload = scan_repo(_tree(tmp_path), parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable))
    claims = _fence_claims(payload)
    assert [c["status"] for c in claims] == ["unresolved", "unresolved", "unresolved"]
    assert "3.9" in claims[0]["why"] and "3.10" in claims[0]["why"]
    assert payload["typecheck"]["checked"] == 0


def test_a_fence_that_is_a_fragment_of_a_longer_example_is_unresolved_not_stale(tmp_path):
    # `client` is defined by an earlier fence, and `await` at top level is only valid in an async body
    fragments = f'{FENCE}python\nclient.decode("{{}}")\n{FENCE}\n\n{FENCE}python\nawait asyncio.sleep(1)\n{FENCE}\n'
    claims = _fence_claims(scan_repo(_tree(tmp_path, fragments), parse_manifest(MANIFEST),
                                     typecheck=Check("mypy", sys.executable)))
    assert [c["status"] for c in claims] == ["unresolved", "unresolved"]
    assert all("longer example" in c["why"] for c in claims)


def test_a_fragment_with_a_real_error_still_fails(tmp_path):
    fence = f'{FENCE}python\nclient.decode("{{}}")\nx: int = "a"\n{FENCE}\n'
    [claim] = _fence_claims(scan_repo(_tree(tmp_path, fence), parse_manifest(MANIFEST),
                                      typecheck=Check("mypy", sys.executable)))
    assert claim["status"] == "fail"


def test_a_top_level_async_with_or_async_for_is_a_fragment_not_stale(tmp_path):
    # the same missing context as a top-level await: the snippet's body runs inside an async function
    fences = ["import asyncio\nasync with asyncio.timeout(1):\n    await asyncio.sleep(0)",
              "async def gen():\n    yield 1\nasync for x in gen():\n    print(x)",
              "import asyncio\nimport json\nasync with asyncio.timeout(1):\n    json.dumps(1).nosuch()"]
    claims = _fence_claims(scan_repo(_tree(tmp_path, "".join(f"{FENCE}python\n{c}\n{FENCE}\n\n" for c in fences)),
                                     parse_manifest(MANIFEST), typecheck=Check("mypy", sys.executable)))
    assert [c["status"] for c in claims] == ["unresolved", "unresolved", "fail"]
    assert "nosuch" in claims[2]["why"]


def test_basedpyright_top_level_async_with_is_a_fragment_not_stale(tmp_path, monkeypatch):
    from dokimi.repos.l0 import typecheck as l0_typecheck

    def fake(argv, cwd, stdin=None):
        if stdin is not None:
            return _completed(returncode=0, stdout=json.dumps([None]))
        if argv[-1] == "--version":
            return _completed(returncode=0, stdout="basedpyright 1.40.2\n")
        report = {"generalDiagnostics": [
            {"file": "fence-0.py", "severity": "error", "message": 'Use of "async" not allowed outside of async function',
             "rule": "reportGeneralTypeIssues", "range": {"start": {"line": 1}}}]}
        return _completed(returncode=1, stdout=json.dumps(report), stderr="")

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    monkeypatch.setattr(l0_typecheck, "python_version", lambda check: (3, 12))
    fence = f"{FENCE}python\nimport asyncio\nasync with asyncio.timeout(1):\n    pass\n{FENCE}\n"
    [claim] = _fence_claims(scan_repo(_tree(tmp_path, fence), parse_manifest(MANIFEST),
                                      typecheck=Check("basedpyright", sys.executable)))
    assert claim["status"] == "unresolved"


def test_the_claim_names_the_first_real_error_when_a_fragment_error_comes_first(tmp_path):
    # `client` is an earlier fence's name: its error is a fragment, so the real error is the one the claim names
    fence = f'{FENCE}python\nclient.decode("{{}}")\nx: int = "a"\n{FENCE}\n'
    [claim] = _fence_claims(scan_repo(_tree(tmp_path, fence), parse_manifest(MANIFEST),
                                      typecheck=Check("mypy", sys.executable)))
    assert claim["status"] == "fail"
    assert "Incompatible types" in claim["why"] and "not defined" not in claim["why"]


def test_basedpyright_top_level_await_return_and_yield_are_fragments_not_stale(tmp_path, monkeypatch):
    # basedpyright words these in its own way (pyright's messages, an optional rule suffix): not mypy's wording
    from dokimi.repos.l0 import typecheck as l0_typecheck

    fences = ["await asyncio.sleep(1)", "return 1", "yield 1"]
    messages = ['"await" allowed only within async function', '"return" can be used only within a function',
                '"yield" not allowed outside of a function or lambda']

    def fake(argv, cwd, stdin=None):
        if stdin is not None:  # the fences parse
            return _completed(returncode=0, stdout=json.dumps([None] * len(fences)))
        if argv[-1] == "--version":
            return _completed(returncode=0, stdout="basedpyright 1.31.6\n")
        report = {"generalDiagnostics": [
            {"file": f"fence-{i}.py", "severity": "error", "message": message, "rule": "reportGeneralTypeIssues",
             "range": {"start": {"line": 0}}} for i, message in enumerate(messages)]}
        return _completed(returncode=1, stdout=json.dumps(report), stderr="")

    monkeypatch.setattr(l0_typecheck, "_run_tool", fake)
    monkeypatch.setattr(l0_typecheck, "python_version", lambda check: (3, 12))
    payload = scan_repo(_tree(tmp_path, "".join(f"{FENCE}python\n{code}\n{FENCE}\n\n" for code in fences)),
                        parse_manifest(MANIFEST), typecheck=Check("basedpyright", sys.executable))
    claims = _fence_claims(payload)
    assert [c["status"] for c in claims] == ["unresolved"] * 3
    assert all("longer example" in c["why"] for c in claims)
    assert payload["typecheck"]["fail"] == 0


def test_the_fence_parser_runs_where_ast_parse_has_no_type_comments_keyword(monkeypatch):
    # Python 3.7's ast.parse takes no type_comments keyword: the fence is parsed without it, not a crash
    import ast
    import io

    from dokimi.repos.l0 import typecheck as l0_typecheck

    real_parse = ast.parse

    def parse_3_7(source, filename="<unknown>", mode="exec", *, feature_version=None):
        return real_parse(source, filename, mode, feature_version=feature_version)

    monkeypatch.setattr(ast, "parse", parse_3_7)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(["x: int = 1", "f("])))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    exec(compile(l0_typecheck._PARSE, "<parse>", "exec"), {"__name__": "__main__"})  # nosec B102 - our own constant
    valid, invalid = json.loads(out.getvalue())
    assert valid is None
    assert invalid is not None


def test_the_scan_command_refuses_a_python_env_that_is_a_directory(tmp_path, capsys):
    # os.access(X_OK) is true for a directory: only a regular file can be the interpreter
    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--typecheck", "--python-env", str(tmp_path)])
    from dokimi.commands.repo import _typecheck_or_exit

    with pytest.raises(SystemExit) as exc:
        _typecheck_or_exit(args)
    assert exc.value.code == 2
    assert "not an executable interpreter" in capsys.readouterr().err
