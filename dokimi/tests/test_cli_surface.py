# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/cli_surface.py — the CLI-interface change report on PRs."""

from __future__ import annotations

import argparse
import enum
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
_SCRIPT = PROJECT / "scripts" / "cli_surface.py"
_spec = importlib.util.spec_from_file_location("cli_surface", _SCRIPT)
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def _parser(extra: bool = False, default: int = 1, drop_cmd: bool = False) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dokimi")
    sub = p.add_subparsers(dest="cmd", required=True)
    if not drop_cmd:
        r = sub.add_parser("run")
        r.add_argument("questions")
        r.add_argument("--n", type=int, default=default)
        r.add_argument("--mode", choices=["a", "b"], default="a")
        if extra:
            r.add_argument("--dry-run", action="store_true")
    g = sub.add_parser("repo")
    gs = g.add_subparsers(dest="repo_cmd", required=True)
    gs.add_parser("scan").add_argument("--hidden", help=argparse.SUPPRESS)
    return p


def _surface(**kw: bool | int) -> dict:
    return cs._command(_parser(**kw), "dokimi")


def test_dump_of_the_real_cli_is_json_and_deterministic():
    first = json.dumps(cs.dump_surface(), sort_keys=True)
    assert first == json.dumps(cs.dump_surface(), sort_keys=True)
    assert "0x" not in first  # no object addresses leaking in from defaults or types
    assert "run" in json.loads(first)["subcommands"]


def test_nested_subcommands_and_hidden_options_are_walked():
    flat = cs._flatten(_surface(), {})
    assert set(flat) == {"dokimi", "dokimi run", "dokimi repo", "dokimi repo scan"}
    (hidden,) = [p for p in flat["dokimi repo scan"]["params"] if p["key"] == "--hidden"]
    assert hidden["hidden"] is True


def test_identical_surfaces_have_no_diff():
    assert cs.diff_surfaces(_surface(), _surface()) == []


@pytest.mark.parametrize("base, head, expected", [
    ({}, {"extra": True}, "+ added option `--dry-run`"),
    ({"extra": True}, {}, "- removed option `--dry-run`"),
    ({}, {"default": 2}, "~ changed option `--n`: default: 1 -> 2"),
    ({}, {"drop_cmd": True}, "- removed command `dokimi run`"),
    ({"drop_cmd": True}, {}, "+ added command `dokimi run`"),
])
def test_each_kind_of_change_is_reported(base, head, expected):
    lines = cs.diff_surfaces(_surface(**base), _surface(**head))
    assert any(expected in line for line in lines), lines


def test_main_diff_exit_codes_and_markdown(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_surface()), encoding="utf-8")
    b.write_text(json.dumps(_surface(extra=True)), encoding="utf-8")
    assert cs.main(["diff", str(a), str(a)]) == 0
    assert "No user-facing CLI surface changes" in capsys.readouterr().out
    assert cs.main(["diff", str(a), str(b), "--markdown"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("## CLI interface change detected") and "```diff" in out


def test_main_dump_writes_file(tmp_path):
    out = tmp_path / "s.json"
    assert cs.main(["dump", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["path"] == "dokimi"


def _positional_parser(order: tuple[str, str], required: bool = True,
                       version: str | None = "1") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dokimi")
    if version:
        p.add_argument("--version", action="version", version=version)
    sub = p.add_subparsers(dest="cmd", required=required)
    c = sub.add_parser("cmp")
    for name in order:
        c.add_argument(name)
    return p


def test_swapping_positionals_is_a_change():
    a = cs._command(_positional_parser(("base", "head")), "dokimi")
    b = cs._command(_positional_parser(("head", "base")), "dokimi")
    assert any("position: 0 -> 1" in line for line in cs.diff_surfaces(a, b))


def test_an_option_before_a_positional_does_not_move_the_positional():
    a = cs._command(_positional_parser(("base", "head")), "dokimi")
    p = argparse.ArgumentParser(prog="dokimi")
    p.add_argument("--version", action="version", version="1")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cmp")
    c.add_argument("--opt")
    c.add_argument("base")
    c.add_argument("head")
    b = cs._command(p, "dokimi")
    assert cs.diff_surfaces(a, b) == [
        "~ `dokimi cmp` parameters changed:",
        "    + added option `--opt`",
    ]


def test_subcommand_required_change_is_reported():
    a = cs._command(_positional_parser(("x", "y")), "dokimi")
    b = cs._command(_positional_parser(("x", "y"), required=False), "dokimi")
    assert any("subcommand required: True -> False" in line for line in cs.diff_surfaces(a, b))


def test_version_option_tracked_but_not_its_value():
    a = cs._command(_positional_parser(("x", "y"), version="1"), "dokimi")
    b = cs._command(_positional_parser(("x", "y"), version="2"), "dokimi")
    c = cs._command(_positional_parser(("x", "y"), version=None), "dokimi")
    assert cs.diff_surfaces(a, b) == []  # a release bump is no CLI change
    assert "    - removed option `--version`" in cs.diff_surfaces(a, c)


def test_added_command_lists_its_params():
    lines = cs.diff_surfaces(_surface(drop_cmd=True), _surface())
    assert "+ added command `dokimi run`" in lines
    assert "    + option `--n`" in lines and "    + argument `questions`" in lines


@pytest.mark.parametrize("old, new, expected", [
    ({"default": Path("a")}, {"default": Path("b")}, "default: 'a' -> 'b'"),
    ({"nargs": "?", "const": "x"}, {"nargs": "?", "const": "y"}, "const: 'x' -> 'y'"),
])
def test_path_defaults_and_const_are_compared(old, new, expected):
    def surface(kw):
        p = argparse.ArgumentParser(prog="dokimi")
        p.add_argument("--opt", **kw)
        return cs._command(p, "dokimi")
    assert any(expected in line for line in cs.diff_surfaces(surface(old), surface(new)))


def _mutually_exclusive_parser(group_required: bool = True) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dokimi")
    sub = p.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    group = run_p.add_mutually_exclusive_group(required=group_required)
    group.add_argument("--tasks", default="", help="Task names")
    group.add_argument("--all", action="store_true", help="All tasks")
    return p


def test_mutually_exclusive_group_membership_is_captured():
    surface = cs._command(_mutually_exclusive_parser(), "dokimi")
    run_cmd = surface["subcommands"]["run"]
    params_by_key = {p["key"]: p for p in run_cmd["params"]}
    # Group key is stable across reordering: based on sorted member keys, not creation order
    group_key = "--all|--tasks"  # sorted pair of the group members
    assert params_by_key["--tasks"]["group_key"] == group_key
    assert params_by_key["--tasks"]["group_required"] is True
    assert params_by_key["--all"]["group_key"] == group_key
    assert params_by_key["--all"]["group_required"] is True


def test_mutually_exclusive_group_required_change_is_reported():
    a = cs._command(_mutually_exclusive_parser(group_required=True), "dokimi")
    b = cs._command(_mutually_exclusive_parser(group_required=False), "dokimi")
    lines = cs.diff_surfaces(a, b)
    assert any("group_required: True -> False" in line for line in lines)


def test_mutually_exclusive_group_membership_removal_is_reported():
    p_with_group = _mutually_exclusive_parser()
    p_without = argparse.ArgumentParser(prog="dokimi")
    sub = p_without.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--tasks", default="")
    run_p.add_argument("--all", action="store_true")

    a = cs._command(p_with_group, "dokimi")
    b = cs._command(p_without, "dokimi")
    lines = cs.diff_surfaces(a, b)
    group_key = "--all|--tasks"
    assert any(f"group_key: '{group_key}' -> None" in line for line in lines)


class _Mode(enum.Enum):
    A = 1
    B = 2


class _Opener:
    """An opaque argparse type with a stable repr. argparse.FileType would be the real one, but its
    construction warns on Python 3.14, and filterwarnings=error makes that a collection error."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def __call__(self, path: str) -> str:
        return path

    def __repr__(self) -> str:
        return f"_Opener({self.mode!r})"


def _make_validator(lo: int):
    def positive(s: str) -> int:
        v = int(s)
        if v < lo:
            raise argparse.ArgumentTypeError("too small")
        return v
    return positive


def _one(**kw) -> dict:
    p = argparse.ArgumentParser(prog="dokimi")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("--n", **kw)
    return cs._command(p, "dokimi")


@pytest.mark.parametrize("old, new", [
    ({"type": _make_validator(1)}, {"type": _make_validator(0)}),
    ({"default": _Mode.A}, {"default": _Mode.B}),
    ({"default": {"a"}}, {"default": {"a", "b"}}),
    ({"type": _Opener("r")}, {"type": _Opener("w")}),
])
def test_opaque_types_and_defaults_are_compared_by_content(old, new):
    assert cs.diff_surfaces(_one(**old), _one(**new))


def test_a_validator_default_is_compared_by_content():
    def with_default(lo: int):
        def positive(s: str, lo: int = lo) -> int:
            if int(s) < lo:
                raise argparse.ArgumentTypeError("too small")
            return int(s)
        return positive

    assert cs.diff_surfaces(_one(type=with_default(1)), _one(type=with_default(0)))


def test_a_validator_keyword_only_default_is_compared_by_content():
    def kw(lim: int):
        def take(s: str, *, lim: int = lim) -> str:
            return s[:lim]
        return take

    assert cs.diff_surfaces(_one(type=kw(3)), _one(type=kw(30)))


def test_a_validator_that_calls_itself_through_a_closure_is_dumped():
    def mk():
        def loop(s: str) -> str:
            return loop(s) if s == "x" else s
        return loop

    assert cs.diff_surfaces(_one(type=mk()), _one(type=mk())) == []


def test_enum_type_is_compared_by_its_members():
    one = enum.Enum("Mode", "a")
    two = enum.Enum("Mode", "a b")
    assert cs.diff_surfaces(_one(type=one), _one(type=two))
    assert cs.diff_surfaces(_one(type=one), _one(type=enum.Enum("Mode", "a"))) == []


def test_class_type_is_compared_by_its_code(tmp_path):
    source = "class Check:\n    def __call__(self, s):\n        return s\n"
    loosened = source.replace("return s", "return s[:1]")
    base = _one(type=_file_validator(tmp_path, "base", source, attr="Check"))
    head = _one(type=_file_validator(tmp_path, "head", loosened, attr="Check"))
    assert cs.diff_surfaces(base, head)


def test_default_objects_are_dumped_without_addresses():
    a = _one(default=object())
    b = _one(default=object())
    assert cs.diff_surfaces(a, b) == []
    assert "0x" not in json.dumps(a)


def test_equal_opaque_values_are_stable():
    kw = {"type": _make_validator(1), "default": {"a", "b"}}
    assert cs.diff_surfaces(_one(**kw), _one(type=_make_validator(1), default={"b", "a"})) == []


def _flag_variants():
    return [
        ({}, {"required": True}, "required: False -> True"),
        ({"type": int}, {"type": str}, "type: 'int' -> 'str'"),
        ({"choices": ["a"]}, {"choices": ["a", "b"]}, "choices:"),
        ({"nargs": "?"}, {"nargs": "*"}, "nargs: '?' -> '*'"),
        ({"action": "store_true"}, {"action": "store_false"}, "action: '_StoreTrueAction' -> '_StoreFalseAction'"),
        ({"action": "store_true"}, {"action": "store_true", "help": argparse.SUPPRESS}, "hidden: False -> True"),
        ({"action": "store_true"}, {"nargs": "?"}, "is_flag: True -> False"),
    ]


@pytest.mark.parametrize("old, new, expected", _flag_variants())
def test_each_field_change_is_reported(old, new, expected):
    lines = cs.diff_surfaces(_one(**old), _one(**new))
    assert any(expected in line for line in lines), lines


def test_reordered_choices_are_no_change():
    assert cs.diff_surfaces(_one(choices=["a", "b"]), _one(choices=["b", "a"])) == []


def test_choices_with_different_types_are_reported():
    # [1, 2] and ['1', '2'] are distinct and should be reported as changed
    assert cs.diff_surfaces(_one(choices=[1, 2]), _one(choices=["1", "2"]))


def test_removed_command_lists_its_options():
    lines = cs.diff_surfaces(_surface(), _surface(drop_cmd=True))
    assert "    - option `--n`" in lines and "    - argument `questions`" in lines


_CLI = """import argparse


def build_parser():
    p = argparse.ArgumentParser(prog="dokimi")
    p.add_argument("--old")
{extra}    return p
"""


def _run_diff_step(tmp_path: Path, *, pr_adds: bool, pr_moves_build_parser: bool = False,
                   truncate_dump: bool = False) -> str:
    """Run the workflow's dump-and-diff step on a merge commit whose base branch moved on.

    With `pr_moves_build_parser` the PR deletes dokimi/cli.py, so the head dump raises.
    """
    import os
    import shutil
    import subprocess
    import sys

    import yaml

    if not all(shutil.which(t) for t in ("bash", "git", "tar")):
        pytest.skip("needs bash, git and tar")
    wf = yaml.safe_load((PROJECT.parent / ".github/workflows/cli-interface.yml").read_text(encoding="utf-8"))
    (step,) = [s for s in wf["jobs"]["cli-surface"]["steps"] if s.get("id") == "diff"]
    repo = tmp_path / "repo"
    pkg = repo / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    (repo / "dokimi" / "scripts").mkdir()
    shutil.copy(_SCRIPT, repo / "dokimi" / "scripts" / "cli_surface.py")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTHON"))}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(extra: str, msg: str, *, cli: bool = True) -> str:
        if cli:
            (pkg / "cli.py").write_text(_CLI.format(extra=extra), encoding="utf-8")
        else:
            (pkg / "cli.py").unlink(missing_ok=True)
        git("add", "-A")
        git("commit", "-q", "--allow-empty", "-m", msg)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    fork_point = commit("", "base")
    commit('    p.add_argument("--pr-opt")\n' if pr_adds else "", "pr",
           cli=not pr_moves_build_parser)
    pr = git("rev-parse", "HEAD")
    git("checkout", "-q", "--detach", fork_point)
    git("merge", "-q", "--no-ff", "-m", "merge", pr)  # what refs/pull/N/merge is
    merge = git("rev-parse", "HEAD")
    git("checkout", "-q", fork_point)
    main_moved = commit('    p.add_argument("--main-opt")\n', "main moves on")
    git("update-ref", "refs/remotes/origin/main", main_moved)
    git("checkout", "-q", "--detach", merge)
    output = tmp_path / "output"
    env.update(BASE_REF="main", RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(output),
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary"),
               PATH=os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", ""))
    script = step["run"]
    if truncate_dump:  # both dumps succeed, then one is cut short: only the diff raises
        marker = "rc=0\n"
        assert marker in script
        script = script.replace(marker, marker + "printf '{' > \"$out/head.json\"\n", 1)
    subprocess.run(["bash", "-e", "-c", script], cwd=repo / "dokimi", env=env, check=True,
                   capture_output=True, text=True)
    return output.read_text(encoding="utf-8")


def test_workflow_ignores_base_branch_changes_made_after_the_merge_commit(tmp_path):
    # main gained --main-opt after the merge commit was made: that is not this PR's change
    assert "changed=0" in _run_diff_step(tmp_path, pr_adds=False)
    assert "changed=1" in _run_diff_step(tmp_path / "b", pr_adds=True)


def test_workflow_reports_error_and_passes_when_the_head_cannot_be_dumped(tmp_path):
    # a PR that moves build_parser: the head dump raises; the step must still exit 0 (checked by
    # _run_diff_step) and report changed=error so the label is left alone
    output = _run_diff_step(tmp_path, pr_adds=False, pr_moves_build_parser=True)
    assert "changed=error" in output
    assert "changed=0" not in output and "changed=1" not in output
    # the github-script step reads diff.md through the file= output: it must be the error text
    diff_md = tmp_path / "cli-surface" / "diff.md"
    assert f"file={diff_md}" in output
    assert "## CLI interface check could not run" in diff_md.read_text(encoding="utf-8")


def test_workflow_reports_error_and_passes_when_the_diff_crashes(tmp_path):
    # an uncaught exception in `diff` must not read as "CLI changed" (changed=1)
    output = _run_diff_step(tmp_path, pr_adds=False, truncate_dump=True)
    assert "changed=error" in output
    assert "changed=0" not in output and "changed=1" not in output


def test_main_diff_of_an_unreadable_dump_exits_2(tmp_path, capsys):
    bad, ok = tmp_path / "bad.json", tmp_path / "ok.json"
    bad.write_text("{", encoding="utf-8")
    ok.write_text(json.dumps(_surface()), encoding="utf-8")
    assert cs.main(["diff", str(bad), str(bad), "--markdown"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "cannot diff" in captured.err
    assert cs.main(["diff", str(ok), str(tmp_path / "missing.json")]) == 2


_VALIDATOR = '''def positive_int(text):
    """Reject non-positive integers."""
    value = int(text)
    if value < 1:  # zero is not positive
        raise ValueError(text)
    return value
'''


def _file_validator(tmp_path: Path, name: str, source: str, attr: str = "positive_int"):
    """`attr` from a module file `validators.py`: same module and qualname every time."""
    path = tmp_path / name / "validators.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("validators", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, attr)


def test_a_validator_is_compared_by_code_not_by_its_docstring_or_comments(tmp_path):
    base = _one(type=_file_validator(tmp_path, "base", _VALIDATOR))
    reworded = _VALIDATOR.replace("Reject non-positive", "Reject zero and negative").replace(
        "# zero is not positive", "# 0 is rejected too").replace("    return value", "\n    return value")
    assert cs.diff_surfaces(base, _one(type=_file_validator(tmp_path, "reworded", reworded))) == []
    loosened = _one(type=_file_validator(tmp_path, "loosened", _VALIDATOR.replace("< 1", "< 0")))
    assert any("type:" in line for line in cs.diff_surfaces(base, loosened))


def test_empty_choices_are_not_unrestricted():
    # choices=[] rejects every value; None accepts any
    assert any("choices: None -> []" in line for line in cs.diff_surfaces(_one(), _one(choices=[])))


def test_moving_a_positional_across_the_subcommand_is_a_change():
    def parser(before: bool) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="dokimi")
        if before:
            p.add_argument("workspace")
        p.add_subparsers(dest="cmd", required=True).add_parser("run")
        if not before:
            p.add_argument("workspace")
        return p
    # `dokimi WS run` becomes `dokimi run WS`
    lines = cs.diff_surfaces(cs._command(parser(True), "dokimi"), cs._command(parser(False), "dokimi"))
    assert any("position: 0 -> 1" in line for line in lines), lines


def test_removing_the_help_option_is_a_change():
    def surface(**kw: bool) -> dict:
        return cs._command(argparse.ArgumentParser(prog="dokimi", **kw), "dokimi")
    assert "    - removed option `--help/-h`" in cs.diff_surfaces(surface(), surface(add_help=False))


def test_allow_abbrev_change_is_reported():
    def surface(abbrev: bool) -> dict:
        return cs._command(argparse.ArgumentParser(prog="dokimi", allow_abbrev=abbrev), "dokimi")
    assert cs.diff_surfaces(surface(True), surface(False)) == [
        "~ `dokimi` option abbreviations allowed: True -> False"]


_CLASS_MEMBERS = {
    "staticmethod": "    @staticmethod\n    def run(s):\n        return BODY\n",
    "classmethod": "    @classmethod\n    def run(cls, s):\n        return BODY\n",
    "property": "    @property\n    def run(self):\n        return BODY\n",
}


@pytest.mark.parametrize("kind", sorted(_CLASS_MEMBERS))
def test_a_class_type_is_compared_by_its_static_method_classmethod_and_property(tmp_path, kind):
    def loaded(name: str, body: str):
        source = "class Check:\n" + _CLASS_MEMBERS[kind].replace("BODY", body)
        return _one(type=_file_validator(tmp_path, f"{kind}-{name}", source, attr="Check"))
    assert cs.diff_surfaces(loaded("base", "1"), loaded("loose", "2"))


def test_a_class_type_is_compared_by_code_it_inherits(tmp_path):
    def loaded(name: str, body: str):
        source = f"class Base:\n    def check(self, s):\n        return {body}\n\nclass Check(Base):\n    pass\n"
        return _one(type=_file_validator(tmp_path, name, source, attr="Check"))
    assert cs.diff_surfaces(loaded("base", "s"), loaded("loose", "s[:1]"))


def test_the_order_of_a_class_members_does_not_change_its_hash(tmp_path):
    ab = "class Check:\n    def a(self, s):\n        return s\n\n    def b(self, s):\n        return s\n"
    ba = "class Check:\n    def b(self, s):\n        return s\n\n    def a(self, s):\n        return s\n"
    assert cs.diff_surfaces(_one(type=_file_validator(tmp_path, "ab", ab, attr="Check")),
                            _one(type=_file_validator(tmp_path, "ba", ba, attr="Check"))) == []


def test_a_class_that_holds_itself_is_dumped(tmp_path):
    cls = _file_validator(tmp_path, "selfref", "class Check:\n    me = None\n\nCheck.me = Check\n",
                          attr="Check")
    assert cs.diff_surfaces(_one(type=cls), _one(type=cls)) == []


def test_a_class_docstring_is_not_a_change(tmp_path):
    base = _file_validator(tmp_path, "doc-base", "class Check:\n    def run(self, s):\n        return s\n",
                           attr="Check")
    reworded = _file_validator(tmp_path, "doc-new", "import os\n\nclass Check:\n    '''Reworded.'''\n"
                               "    def run(self, s):\n        return s\n", attr="Check")
    assert cs.diff_surfaces(_one(type=base), _one(type=reworded)) == []


def test_an_enum_member_without_a_stable_repr_is_dumped_the_same_every_time(tmp_path):
    # two loads of the same source: the members' objects live at different addresses
    src = "import enum\n\nclass Obj(enum.Enum):\n    a = object()\n"
    one = _one(type=_file_validator(tmp_path, "enum-1", src, attr="Obj"))
    two = _one(type=_file_validator(tmp_path, "enum-2", src, attr="Obj"))
    assert cs.diff_surfaces(one, two) == []


def test_a_subclass_override_wins_over_the_base_definition(tmp_path):
    def loaded(name: str, body: str):
        source = (f"class Base:\n    def check(self, s):\n        return s\n\n"
                  f"class Check(Base):\n    def check(self, s):\n        return {body}\n")
        return _one(type=_file_validator(tmp_path, name, source, attr="Check"))
    assert cs.diff_surfaces(loaded("strict", "s"), loaded("loose", "True"))


def test_classes_with_the_same_members_and_different_bases_differ(tmp_path):
    def loaded(name: str, base: str):
        source = f"class A:\n    pass\n\nclass B:\n    pass\n\nclass Check({base}):\n    pass\n"
        return _one(type=_file_validator(tmp_path, name, source, attr="Check"))
    assert cs.diff_surfaces(loaded("a", "A"), loaded("b", "B"))


def test_a_builtin_base_of_a_class_type_is_a_change(tmp_path):
    def loaded(name: str, base: str):
        return _one(type=_file_validator(tmp_path, name, f"class Check({base}):\n    pass\n", attr="Check"))
    assert cs.diff_surfaces(loaded("i", "int"), loaded("s", "str"))


def test_a_property_setter_only_change_is_reported(tmp_path):
    def loaded(name: str, body: str):
        source = ("class Check:\n    @property\n    def v(self):\n        return 1\n\n"
                  f"    @v.setter\n    def v(self, x):\n        {body}\n")
        return _one(type=_file_validator(tmp_path, name, source, attr="Check"))
    assert cs.diff_surfaces(loaded("a", "pass"), loaded("b", "self.x = x"))


def test_a_set_default_is_dumped_in_the_same_order_under_any_hash_seed():
    import os
    import subprocess
    import sys

    code = ("import importlib.util, json, sys\n"
            "spec = importlib.util.spec_from_file_location('cs', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "print(json.dumps(m._value(frozenset({'alpha', 'beta', 'gamma'}))))\n")
    outs = {
        subprocess.run([sys.executable, "-c", code, str(_SCRIPT)], capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed}).stdout
        for seed in ("1", "2", "3", "4")
    }
    assert len(outs) == 1, outs
