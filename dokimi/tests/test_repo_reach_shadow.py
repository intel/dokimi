# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The reach probe's shadow scenarios (`--scenarios shadow`): no LLM.

A fake harness stands in for the CLI. In each directory of its start chain, and of the
chain of a file it is told to read, it loads the directory's own ``CLAUDE*`` files, or its
``AGENTS.md`` when there is none, and it logs the files each run's workspace holds.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from tests.test_repo_reach import _can_symlink, _write

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.cli import build_parser
from dokimi.repos import WorkTree, load_manifest
from dokimi.repos import reach
from dokimi.repos.harness_profile import ProfileError, load_profile, parse_profile
from dokimi.repos.reach import (ReachError, SeededFile, Shadow, distinct_salt, format_matrix, run_reach, scenarios,
                                shadow_for, token)
from dokimi.repos.source import SourceArgumentError

HARNESS = r'''
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("1.2.3"); sys.exit(0)
prompt, tools = args[args.index("-p") + 1], args[args.index("--tools") + 1]
cwd = Path.cwd().resolve()
root = next(p for p in [cwd, *cwd.parents] if (p / ".git").exists())
held = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
              if p.is_file() and ".git" not in p.relative_to(root).parts)
with open(Path(__file__).with_name("seen.jsonl"), "a", encoding="utf-8") as log:
    log.write(json.dumps({"prompt": prompt, "cwd": cwd.relative_to(root).as_posix(), "held": held}) + "\n")
if os.environ.get("FAKE_MODE") == "crash":
    print("boom", file=sys.stderr); sys.exit(3)
def chain(d):
    out = [d]
    while d != root:
        d = d.parent; out.append(d)
    return out
def own(d):
    claude = [d / n for n in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md") if (d / n).is_file()]
    return claude or [p for p in [d / "AGENTS.md"] if p.is_file()]
dirs = chain(cwd)
match = re.search(r"read the file (\S+),", prompt)
if match and tools == "Read":
    dirs += chain((root / match.group(1)).parent)
text = "".join(p.read_text() for d in dict.fromkeys(dirs) for p in own(d))
answer = "\n".join(dict.fromkeys(re.findall(r"RC[0-9A-F]{6}", text))) or "NONE"
for e in [{"type": "system", "subtype": "init", "claude_code_version": "1.2.3"},
          {"type": "result", "subtype": "success", "is_error": False, "result": answer, "total_cost_usd": 0.001,
           "num_turns": 1, "usage": {"input_tokens": 1, "output_tokens": 1}, "permission_denials": []}]:
    print(json.dumps(e))
'''

NAMES = ["CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"]


def _raw(tmp_path: Path, **guidance) -> dict:
    script = tmp_path / "harness.py"
    script.write_text(HARNESS, encoding="utf-8")
    return {"schema": "harness_profile.v1", "name": "fake", "bin": sys.executable,
            "version_cmd": ["{bin}", str(script), "--version"],
            "invoke": ["{bin}", str(script), "-p", "{prompt}", "--model", "{model}", "{tools}"],
            "tools": {"none": ["--tools", ""], "read": ["--tools", "Read"]},
            "config_dir_env": "FAKE_CONFIG_DIR", "env_allow": ["PATH", "FAKE_MODE", "LD_LIBRARY_PATH"],
            "trace": "claude-stream-json", "guidance": {"names": NAMES, **guidance}}


def _profile(tmp_path: Path, **guidance) -> Path:
    path = tmp_path / "fake.yaml"
    path.write_text(yaml.safe_dump(_raw(tmp_path, **guidance)), encoding="utf-8")
    return path


def _tree(tmp_path: Path, extra: tuple[str, ...] = ()) -> Path:
    root = tmp_path / "repo"
    for rel in ("AGENTS.md", "sub/AGENTS.md", *extra):
        _write(root, rel, f"# {rel}\nOne line.\n")
    _write(root, "evals/repo-eval.yaml", yaml.safe_dump({
        "schema": "repo_manifest.v1", "id": "toy", "eval_dir": "evals", "tasks": "evals/tasks",
        "guidance": {"globs": ["**/AGENTS.md"]}, "entry_points": {"grade": "python3 evals/grade.py {run_dir}"}}))
    _write(root, "evals/tasks/.keep", "")
    return root


def _run(tmp_path: Path, monkeypatch, kinds=("root", "read", "shadow"), mode="ok", **guidance):
    monkeypatch.setenv("FAKE_MODE", mode)
    tree = WorkTree(_tree(tmp_path))
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    profile = load_profile(_profile(tmp_path, **({"shadow_file": "CLAUDE.local.md"} | guidance)))
    return run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"), profile=profile,
                     model="m", out_dir=tmp_path / "out", work_dir=work, salt="s", kinds=kinds, keep=True)


def _seen(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "seen.jsonl").read_text(encoding="utf-8").splitlines()]


# --- the profile key -----------------------------------------------------------


def test_only_the_claude_code_profile_declares_a_shadow_file():
    assert load_profile("claude-code").shadow_file == "CLAUDE.local.md"
    assert [load_profile(n).shadow_file for n in ("codex", "copilot", "copilot-review")] == [None, None, None]


@pytest.mark.parametrize("value, ok", [("CLAUDE.local.md", True), (".claude/CLAUDE.md", True), (3, False), ("", False)])
def test_the_profile_schema_takes_a_shadow_file_string(tmp_path, value, ok):
    raw = _raw(tmp_path, shadow_file=value)
    if ok:
        assert parse_profile(raw).shadow_file == value
    else:
        with pytest.raises(ProfileError, match="shadow_file"):
            parse_profile(raw)


# --- the scenario list -----------------------------------------------------------

FILES = [".github/instructions/c.md", "AGENTS.md", "only/CLAUDE.md", "sub/AGENTS.md", "sub/CLAUDE.md"]


def test_shadow_scenarios_cover_the_root_and_each_directory_with_a_non_claude_file():
    shadow = Shadow("CLAUDE.local.md", frozenset())
    plan = scenarios(FILES, ("shadow",), shadow=shadow)
    assert [(s.id, s.cwd, s.tools, s.read, s.shadow) for s in plan] == [
        ("shadow=.", "", "none", None, "CLAUDE.local.md"),
        ("shadow=.github/instructions", "", "read", ".github/instructions/REACH_PROBE.txt",
         ".github/instructions/CLAUDE.local.md"),
        ("shadow=sub", "", "read", "sub/REACH_PROBE.txt", "sub/CLAUDE.local.md"),
    ]
    assert plan[2].prompt == scenarios(FILES, ("read",))[-1].prompt  # told to read, as read=sub is
    assert not [s for s in scenarios(FILES, shadow=shadow) if s.shadow]  # not in the default kinds
    with pytest.raises(ReachError, match="shadow_file"):
        scenarios(FILES, ("root", "shadow"))


@pytest.mark.parametrize("held, skipped", [
    ("sub/CLAUDE.local.md", "shadow=sub"),
    ("CLAUDE.local.md", "shadow=."),
    ("sub/claude.local.MD", "shadow=sub"),  # one file on a case-insensitive disk
    ("sub/CLAUDE.local.md/notes.txt", "shadow=sub"),  # a directory at the path
])
def test_a_tree_path_at_the_shadow_path_skips_that_scenario(tmp_path, held, skipped):
    root = _tree(tmp_path, (held,))
    shadow = shadow_for(load_profile(_profile(tmp_path, shadow_file="CLAUDE.local.md")), WorkTree(root), ("shadow",))
    ids = [s.id for s in scenarios(["AGENTS.md", "sub/AGENTS.md"], ("shadow",), shadow=shadow)]
    assert ids == [i for i in ["shadow=.", "shadow=sub"] if i != skipped]


def test_a_tree_file_above_the_shadow_path_skips_that_scenario(tmp_path):
    shadow = shadow_for(load_profile(_profile(tmp_path, shadow_file="AGENTS.md/local.md")), WorkTree(_tree(tmp_path)),
                        ("shadow",))
    ids = [s.id for s in scenarios(["AGENTS.md", "sub/AGENTS.md", "x/GEMINI.md"], ("shadow",), shadow=shadow)]
    assert ids == ["shadow=x"]  # x/AGENTS.md is no tree file, so x/AGENTS.md/local.md is free


@pytest.mark.parametrize("held, skipped", [("CLAUDE.local.md", "shadow=."), ("sub/CLAUDE.local.md", "shadow=sub")])
def test_an_empty_directory_at_the_shadow_path_skips_that_scenario(tmp_path, held, skipped):
    root = _tree(tmp_path)
    (root / held).mkdir()  # in no file list: only the disk shows it
    shadow = shadow_for(load_profile(_profile(tmp_path, shadow_file="CLAUDE.local.md")), WorkTree(root), ("shadow",))
    ids = [s.id for s in scenarios(["AGENTS.md", "sub/AGENTS.md"], ("shadow",), shadow=shadow)]
    assert ids == [i for i in ["shadow=.", "shadow=sub"] if i != skipped]


@pytest.mark.parametrize("error", [
    SourceArgumentError("path escapes the repository: CLAUDE.local.md"),
    RuntimeError("Symlink loop from 'CLAUDE.local.md'"),  # Path.resolve on a link loop before Python 3.13
    OSError(40, "Too many levels of symbolic links"),
])
def test_a_shadow_path_the_tree_cannot_resolve_is_not_free(error):
    class Unresolvable:
        def is_dir(self, path):
            raise error

    assert not Shadow("CLAUDE.local.md", frozenset(), Unresolvable()).free("CLAUDE.local.md")


@pytest.fixture(params=["this-python", "before-3.13"])
def looping_link_tree(tmp_path, monkeypatch, request) -> tuple[Path, bool]:
    """A git working tree whose ``CLAUDE.local.md`` is an ignored link to itself, so in no file list; and whether
    resolving it raises. Python before 3.13 raises RuntimeError; 3.13 hands back the link, which is no directory.
    ``before-3.13`` makes ``Path.resolve`` raise as it did there, on any Python."""
    root = _tree(tmp_path)
    if not _can_symlink(tmp_path):
        pytest.skip("this OS refuses an unprivileged symlink")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("CLAUDE.local.md\n", encoding="utf-8")
    (root / "CLAUDE.local.md").symlink_to("CLAUDE.local.md")
    assert "CLAUDE.local.md" not in WorkTree(root).files()
    if request.param == "before-3.13":
        resolve = Path.resolve

        def looping(self, strict=False):
            out = resolve(self, strict=strict)
            if out.is_symlink():  # a resolve that ends on a link stopped at a loop
                raise RuntimeError(f"Symlink loop from {str(self)!r}")
            return out

        monkeypatch.setattr(Path, "resolve", looping)
    try:
        (root / "CLAUDE.local.md").resolve()
    except (RuntimeError, OSError):
        return root, True
    return root, False


def test_an_ignored_looping_link_at_the_shadow_path_is_no_traceback(tmp_path, looping_link_tree):
    root, raises = looping_link_tree
    shadow = shadow_for(load_profile(_profile(tmp_path, shadow_file="CLAUDE.local.md")), WorkTree(root), ("shadow",))
    ids = [s.id for s in scenarios(["AGENTS.md", "sub/AGENTS.md"], ("shadow",), shadow=shadow)]
    assert ids == (["shadow=sub"] if raises else ["shadow=.", "shadow=sub"])  # a loop it cannot follow is not free


def test_repo_reach_dry_run_over_an_ignored_looping_link_exits_cleanly(tmp_path, capsys, looping_link_tree):
    _, raises = looping_link_tree
    args = build_parser().parse_args(_cli(tmp_path, "--scenarios", "shadow", shadow_file="CLAUDE.local.md"))
    assert args.func(args) in (None, 0)
    ids = [r["scenario"] for r in json.loads(capsys.readouterr().out)["runs"]]
    assert ids == (["shadow=sub"] if raises else ["shadow=.", "shadow=sub"])


def test_shadow_for_refuses_a_profile_without_one_and_an_unsafe_path(tmp_path):
    tree = WorkTree(_tree(tmp_path))
    bare = load_profile(_profile(tmp_path))
    assert shadow_for(bare, tree, ("root", "read")) is None
    with pytest.raises(ReachError, match="--scenarios shadow needs guidance.shadow_file in the harness profile; fake"):
        shadow_for(bare, tree, ("shadow",))
    for unsafe in ("../CLAUDE.local.md", "/etc/x", ".git/hooks/post-checkout"):
        with pytest.raises(ReachError, match="not a path inside a directory"):
            shadow_for(load_profile(_profile(tmp_path, shadow_file=unsafe)), tree, ("shadow",))


def test_distinct_salt_counts_the_shadow_tokens(tmp_path):
    root = tmp_path / "t"
    _write(root, "AGENTS.md", f"mentions {token('s', 'sub/CLAUDE.local.md', 'tail')}\n")
    tree = WorkTree(root)
    assert distinct_salt(tree, ["AGENTS.md"], "s") == "s"
    assert distinct_salt(tree, ["AGENTS.md"], "s", shadows=("sub/CLAUDE.local.md",)) == "s-1"


# --- end to end with the fake harness -------------------------------------------


@pytest.mark.parametrize("name", ["CLAUDE.local.md", ".claude/CLAUDE.md"])
def test_the_shadow_file_is_in_its_own_scenario_only(tmp_path, monkeypatch, name):
    payload = _run(tmp_path, monkeypatch, shadow_file=name)
    ids = [s["id"] for s in payload["scenarios"]]
    assert ids == ["cwd=.", "read=sub", "shadow=.", "shadow=sub"]
    shadows = {"shadow=.": name, "shadow=sub": f"sub/{name}"}
    for scenario_id, seen in zip(ids, _seen(tmp_path), strict=True):  # one log line per harness run
        written = [p for p in seen["held"] if p.endswith(name)]
        assert written == ([shadows[scenario_id]] if scenario_id in shadows else []), scenario_id
    ws = Path(payload["workspace"])
    assert not [p for p in ws.rglob("*") if p.name in (name, ".claude")]  # removed, with the directory it made
    tracked = subprocess.run(["git", "-C", str(ws), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert name not in tracked


def test_the_shadow_result_is_on_its_scenario_row(tmp_path, monkeypatch):
    payload = _run(tmp_path, monkeypatch)
    validate_artifact("guidance_reach", {**payload, "schema_version": "guidance_reach.v1"})
    rows = {s["id"]: s for s in payload["scenarios"]}
    assert rows["shadow=."]["shadow"] == {
        "path": "CLAUDE.local.md", "tokens": [token("s", "CLAUDE.local.md", "head"), token("s", "CLAUDE.local.md", "tail")],
        "head": True, "tail": True, "read_by_tool": False, "class": "loaded"}
    assert rows["shadow=sub"]["shadow"]["class"] == "loaded"
    assert "shadow" not in rows["cwd=."] and "shadow" not in rows["read=sub"]
    assert all(not s["unknown"] for s in payload["scenarios"])  # its own tokens are not unknown
    m = payload["summary"]["matrix"]
    assert sorted(m) == ["AGENTS.md", "sub/AGENTS.md"] and [f["path"] for f in payload["files"]] == sorted(m)
    assert m["AGENTS.md"] == {"cwd=.": "loaded", "read=sub": "loaded", "shadow=.": "not_loaded", "shadow=sub": "loaded"}
    assert m["sub/AGENTS.md"] == {"cwd=.": "not_loaded", "read=sub": "loaded", "shadow=.": "not_loaded",
                                  "shadow=sub": "not_loaded"}
    text = format_matrix(payload)
    assert "shadow file of shadow=sub: sub/CLAUDE.local.md (written for this scenario only) loaded" in text


def test_a_failed_shadow_scenario_records_an_error_and_removes_the_file(tmp_path, monkeypatch):
    payload = _run(tmp_path, monkeypatch, kinds=("shadow",), mode="crash")
    assert [s["shadow"]["class"] for s in payload["scenarios"]] == ["error", "error"]
    assert "shadow file of shadow=.: CLAUDE.local.md (written for this scenario only) error" in format_matrix(payload)
    assert not list(Path(payload["workspace"]).rglob("CLAUDE.local.md"))


def test_the_shadow_file_is_removed_when_its_scenario_raises(tmp_path, monkeypatch):
    real, held = reach._run_one, []

    def run_one(profile, scenario, model, workspace, *rest):
        if scenario.id == "shadow=sub":
            held.append((workspace / "sub/CLAUDE.local.md").is_file())
            raise RuntimeError("killed")
        return real(profile, scenario, model, workspace, *rest)

    monkeypatch.setattr(reach, "_run_one", run_one)
    with pytest.raises(RuntimeError, match="killed"):
        _run(tmp_path, monkeypatch, kinds=("shadow",))
    ws = next((tmp_path / "work").iterdir()) / "ws"
    assert held == [True] and (ws / "sub/AGENTS.md").is_file() and not list(ws.rglob("CLAUDE.local.md"))


def test_a_shadow_file_never_replaces_a_probe_file(tmp_path, monkeypatch):
    with pytest.raises(ReachError, match="would replace a file of the probe workspace"):
        _run(tmp_path, monkeypatch, kinds=("shadow",), shadow_file="REACH_PROBE.txt")
    ws = next((tmp_path / "work").iterdir()) / "ws"
    assert (ws / "REACH_PROBE.txt").read_text(encoding="utf-8") == "probe\n"


def _workspace_files(ws: Path) -> dict[str, str]:
    return {p.relative_to(ws).as_posix(): p.read_text(encoding="utf-8") for p in ws.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(ws).parts}


def test_a_shadow_file_never_goes_under_a_probe_file(tmp_path, monkeypatch):
    with pytest.raises(ReachError, match="the shadow file REACH_PROBE.txt/x.md needs REACH_PROBE.txt, a file of the "
                                         "probe workspace, as a directory"):
        _run(tmp_path, monkeypatch, kinds=("shadow",), shadow_file="REACH_PROBE.txt/x.md")
    files = _workspace_files(next((tmp_path / "work").iterdir()) / "ws")
    assert sorted(files) == ["AGENTS.md", "REACH_PROBE.txt", "sub/AGENTS.md", "sub/REACH_PROBE.txt"]
    assert files["REACH_PROBE.txt"] == "probe\n"


@pytest.mark.parametrize("path", ["CLAUDE.local.md", "a/b/CLAUDE.local.md"])
def test_a_failed_shadow_write_removes_what_it_made(tmp_path, monkeypatch, path):
    def write_part(self, text, **kw):
        with open(self, "w", encoding="utf-8") as f:
            f.write(text[:5])
        raise OSError(28, "No space left on device")

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(Path, "write_text", write_part)
    with pytest.raises(ReachError, match=f"cannot write the shadow file {path} in the probe workspace: .*No space"):
        with reach._shadowed(ws, SeededFile(path, "RC000001", "RC000002")):
            pass
    assert list(ws.iterdir()) == []


def test_a_failed_shadow_directory_removes_nothing_it_did_not_make(tmp_path, monkeypatch):
    def lose_race(self, *a, **kw):  # another writer puts a file where the directory goes
        self.write_bytes(b"theirs")
        raise FileExistsError(17, "File exists", str(self))

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(Path, "mkdir", lose_race)
    with pytest.raises(ReachError, match="cannot write the shadow file a/CLAUDE.local.md in the probe workspace"):
        with reach._shadowed(ws, SeededFile("a/CLAUDE.local.md", "RC000001", "RC000002")):
            pass
    assert (ws / "a").read_bytes() == b"theirs"


def test_a_bad_shadow_path_is_refused_before_any_harness_call(tmp_path, monkeypatch):
    real, calls = reach._run_one, []

    def run_one(*args):
        calls.append(args[1].id)
        return real(*args)

    monkeypatch.setattr(reach, "_run_one", run_one)
    with pytest.raises(ReachError, match="the shadow file REACH_PROBE.txt/x.md needs REACH_PROBE.txt"):
        _run(tmp_path, monkeypatch, kinds=("root", "cwd", "read", "shadow"), shadow_file="REACH_PROBE.txt/x.md")
    assert calls == [] and not (tmp_path / "seen.jsonl").exists()  # no scenario ran, so none was billed


def test_the_receipt_leaves_the_shadow_scenarios_out(tmp_path, monkeypatch):
    from dokimi.repos.budget import budget
    from dokimi.repos.receipt import build_receipt

    payload = _run(tmp_path, monkeypatch, kinds=("root", "shadow"))
    assert [s["id"] for s in payload["scenarios"]] == ["cwd=.", "shadow=.", "shadow=sub"]
    tree = WorkTree(tmp_path / "repo")
    receipt = build_receipt(payload, budget(tree, tuple(NAMES), 5), tree)
    assert [r["run_id"] for r in receipt["receipts"]] == ["s:cwd=."]  # the tree as committed, no shadow file


def test_the_reach_schema_checks_the_shadow_record(tmp_path, monkeypatch):
    payload = {**_run(tmp_path, monkeypatch, kinds=("shadow",)), "schema_version": "guidance_reach.v1"}
    payload["scenarios"][0]["shadow"]["class"] = "seen"
    with pytest.raises(ArtifactValidationError, match="seen"):
        validate_artifact("guidance_reach", payload)


# --- the command ------------------------------------------------------------------


def _cli(tmp_path: Path, *extra: str, **guidance) -> list[str]:
    return ["repo", "reach", str(_tree(tmp_path)), "--manifest", "evals/repo-eval.yaml", "--harness",
            str(_profile(tmp_path, **guidance)), "--model", "m", "--dry-run", *extra]


def test_repo_reach_dry_run_lists_shadow_scenarios_only_when_asked(tmp_path, capsys):
    args = build_parser().parse_args(_cli(tmp_path, shadow_file="CLAUDE.local.md"))
    args.func(args)
    assert [r["scenario"] for r in json.loads(capsys.readouterr().out)["runs"]] == ["cwd=.", "cwd=sub", "read=sub"]
    args = build_parser().parse_args(_cli(tmp_path, "--scenarios", "shadow", shadow_file="CLAUDE.local.md"))
    args.func(args)
    runs = json.loads(capsys.readouterr().out)["runs"]
    assert [(r["scenario"], r["shadow"]) for r in runs] == [("shadow=.", "CLAUDE.local.md"),
                                                            ("shadow=sub", "sub/CLAUDE.local.md")]
    assert runs[1]["argv"][-2:] == ["--tools", "Read"]


def test_repo_reach_dry_run_plans_no_scenario_for_a_link_a_run_drops(tmp_path, capsys):
    argv = _cli(tmp_path, "--scenarios", "root,cwd,read,shadow", shadow_file="CLAUDE.local.md")
    if not _can_symlink(tmp_path):
        pytest.skip("this OS refuses an unprivileged symlink")
    agents = tmp_path / "repo" / "sub" / "AGENTS.md"
    agents.unlink()
    agents.symlink_to("nowhere.md")  # a run lists it in unresolved and probes nothing in sub/
    args = build_parser().parse_args(argv)
    args.func(args)
    assert [r["scenario"] for r in json.loads(capsys.readouterr().out)["runs"]] == ["cwd=.", "shadow=."]
    args = build_parser().parse_args([*argv, "--only", "read=sub"])
    with pytest.raises(SystemExit) as exc:  # --only checks against the same list
        args.func(args)
    assert exc.value.code == 2 and "--only read=sub matches no scenario" in capsys.readouterr().err


def test_repo_reach_refuses_shadow_for_a_profile_without_a_shadow_file(tmp_path, capsys):
    args = build_parser().parse_args(_cli(tmp_path, "--scenarios", "root,shadow"))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "--scenarios shadow needs guidance.shadow_file in the harness profile; fake sets none" in capsys.readouterr().err


def test_repo_reach_reports_a_shadow_file_the_workspace_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "ok")
    (tmp_path / "work").mkdir()
    argv = [a for a in _cli(tmp_path, "--scenarios", "shadow", "--work-dir", str(tmp_path / "work"), "--out",
                            str(tmp_path / "out"), shadow_file="REACH_PROBE.txt/x.md") if a != "--dry-run"]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert ("✗ the shadow file REACH_PROBE.txt/x.md needs REACH_PROBE.txt, a file of the probe workspace, as a "
            "directory") in capsys.readouterr().err
