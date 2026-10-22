# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for checks across task bases (repos/bases.py) and repo_static.v1 (repos/static.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos import RepoSource, check_repo, load_manifest, open_source
from dokimi.repos.source import WorkTree, base_opener
from dokimi.repos.static import check_static, static_counts

MANIFEST = {
    "schema": "repo_manifest.v1",
    "id": "toy",
    "source": {"url": "https://example.invalid/toy"},
    "eval_dir": "dev/evals",
    "guidance": {"globs": ["**/AGENTS.md", ".github/instructions/*.md"], "also_scan": ["CONTRIBUTING.md"]},
    "image": {"dockerfile": "dev/evals/Dockerfile"},
    "entry_points": {"grade": "python3 {eval_dir}/run.py grade --contract {run_dir}"},
    "tasks": "dev/evals/tasks",
}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _task(root: Path, task_id: str, base: str) -> None:
    body = {"id": task_id, "family": "fix", "base": base, "grader": "tests", "prompt": "Fix the bug."}
    _write(root, f"dev/evals/tasks/{task_id}/task.json", json.dumps(body))


def _commit_all(root: Path, msg: str) -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    if not (root / ".git").exists():
        git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return git("rev-parse", "HEAD")


def _run_cli(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_globs_and_bases_are_checked_at_every_task_base(tmp_path, capsys):
    root = tmp_path / "toy"
    _write(root, "src/lib.c", "int f(void);\n")
    bare = _commit_all(root, "code only")
    _write(root, "AGENTS.md", "# guidance\n")
    _write(root, ".github/instructions/c.md", "rules\n")
    old = _commit_all(root, "guidance")
    (root / ".github/instructions/c.md").unlink()  # deleted upstream, like oneDAL after #3812
    _write(root, "CONTRIBUTING.md", "contrib\n")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(MANIFEST))
    for task_id, base in (("fix_old", old), ("fix_older", f"{old}^"), ("fix_bare", bare)):
        _task(root, task_id, base)
    _commit_all(root, "evals")

    argv = ["repo", "validate", f"{root}@HEAD", "--manifest", "dev/evals/repo-eval.yaml", "--json"]
    _run_cli(argv)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True, out["errors"]
    assert out["warnings"] == []  # .github/instructions/*.md still matches at a task base
    assert out["guidance_files"] == ["AGENTS.md"]
    assert out["guidance_by_base"] == {old: 2, f"{old}^": 0, bare: 0}
    assert len(out["notes"]) == 1 and "fix_bare, fix_older" in out["notes"][0]

    _task(root, "fix_gone", "f" * 40)
    _commit_all(root, "unknown base")
    with pytest.raises(SystemExit):
        _run_cli(argv)
    out = json.loads(capsys.readouterr().out)
    assert any("fix_gone" in e and "does not resolve" in e for e in out["errors"]), out["errors"]


def test_glob_matching_nothing_anywhere_still_warns(tmp_path):
    root = tmp_path / "toy"
    raw = json.loads(json.dumps(MANIFEST))
    raw["guidance"]["globs"].append(".github/copilot-instructions.md")
    _write(root, "AGENTS.md", "# guidance\n")
    _write(root, ".github/instructions/c.md", "rules\n")
    _write(root, "CONTRIBUTING.md", "contrib\n")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(raw))
    base = _commit_all(root, "repo")
    _task(root, "fix_one", base)
    _commit_all(root, "task")

    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    report = check_repo(tree, load_manifest(tree, "dev/evals/repo-eval.yaml"), base_opener(source, tree))
    assert report.ok, report.errors
    assert report.warnings == ["guidance glob matches no file at the eval tree or at any task base: "
                               ".github/copilot-instructions.md"]


def test_work_tree_without_git_skips_the_base_check(tmp_path, capsys):
    root = tmp_path / "toy"
    _write(root, "AGENTS.md", "# guidance\n")
    _write(root, ".github/instructions/c.md", "rules\n")
    _write(root, "CONTRIBUTING.md", "contrib\n")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(MANIFEST))
    _task(root, "fix_one", "0" * 40)
    _run_cli(["repo", "validate", str(root), "--manifest", "dev/evals/repo-eval.yaml", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["guidance_by_base"] is None
    assert out["notes"] == ["task bases were not checked: the source has no git history"]


def test_shallow_clone_checks_the_bases_it_has_and_names_the_cause(tmp_path, capsys):
    root = tmp_path / "toy"
    _write(root, "AGENTS.md", "# guidance\n")
    _write(root, "CONTRIBUTING.md", "contrib\n")
    old = _commit_all(root, "repo")
    _write(root, "src/lib.c", "int f(void);\n")
    recent = _commit_all(root, "code")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(MANIFEST))
    _task(root, "fix_recent", recent)
    _task(root, "fix_old", old)
    _commit_all(root, "evals")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "2", root.as_uri(), str(shallow)], check=True)

    with pytest.raises(SystemExit):
        _run_cli(["repo", "validate", str(shallow), "--manifest", "dev/evals/repo-eval.yaml", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["guidance_by_base"] == {recent: 1}
    assert len(out["errors"]) == 1 and "fix_old" in out["errors"][0], out["errors"]
    assert "shallow" in out["errors"][0] and "fetch-depth: 0" in out["errors"][0]
    assert out["warnings"] == ["guidance glob matches no file at the eval tree or at any of the 1 "
                               "task base(s) that resolved: .github/instructions/*.md"]


def test_repo_static_v1():
    doc = {"schema": "repo_static.v1", "files": ["AGENTS.md"],
           "counts": {"ok": 1, "ok_basename": 1, "unresolved": 0, "fail": 1},
           "claims": [{"file": "AGENTS.md", "line": 3, "kind": "path", "claim": "a.h", "status": "ok"},
                      {"file": "AGENTS.md", "line": 4, "kind": "path", "claim": "b.h", "status": "ok_basename"},
                      {"file": "AGENTS.md", "line": 9, "kind": "target", "claim": "//x", "status": "fail",
                       "why": "no such target", "ctx": "bazel test //x"}]}
    assert check_static(doc) == []
    assert static_counts(doc) == {"ok": 2, "unresolved": 0, "fail": 1}
    bad = {"schema": "repo_static.v1", "claims": [{"file": "a", "status": "broken"}]}
    problems = check_static(bad)
    assert any("'line' is a required property" in p for p in problems), problems
    assert any("broken" in p for p in problems), problems
    assert check_static({"files": []}) != []


def test_work_tree_lists_what_git_shows(tmp_path):
    root = tmp_path / "wt"
    _write(root, "AGENTS.md", "root\n")
    _write(root, ".gitignore", ".venv/\n")
    _commit_all(root, "init")
    _write(root, ".venv/pkg/AGENTS.md", "vendored\n")
    _write(root, "new/AGENTS.md", "not committed yet\n")
    assert WorkTree(root).files() == [".gitignore", "AGENTS.md", "new/AGENTS.md"]
    _write(root, "sub/AGENTS.md", "sub\n")
    _write(root, "sub/.venv/AGENTS.md", "vendored below a subdirectory\n")
    assert WorkTree(root / "sub").files() == ["AGENTS.md"]  # a subdirectory of the checkout
    plain = tmp_path / "plain"
    _write(plain, ".venv/AGENTS.md", "no git here, so every file counts\n")
    assert WorkTree(plain).files() == [".venv/AGENTS.md"]


@pytest.mark.parametrize(("text", "why"), [("{", "not valid JSON"), ("[]", "not a JSON object"),
                                          ('{"mcpServers": []}', "`mcpServers` is not an object"),
                                          pytest.param("[" * 100000, "not valid JSON", id="deeply-nested")])
def test_validate_warns_about_a_malformed_mcp_json_at_a_task_base_and_at_a_tree_without_history(tmp_path, capsys,
                                                                                              text, why):
    root = tmp_path / "toy"
    _write(root, ".mcp.json", text)
    _write(root, "src/lib.c", "int f(void);\n")
    base = _commit_all(root, "code")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(MANIFEST))
    _task(root, "fix_one", base)
    _commit_all(root, "evals")
    for source in (f"{root}@HEAD", str(root)):  # with git history (the task base), and a plain work tree
        if source == str(root):
            subprocess.run(["rm", "-rf", str(root / ".git")], check=True)
        _run_cli(["repo", "validate", source, "--manifest", "dev/evals/repo-eval.yaml", "--json"])  # succeeds
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True, out["errors"]
        assert any(".mcp.json" in w and why in w for w in out["warnings"]), out["warnings"]


def test_validate_accepts_a_valid_or_absent_mcp_json(tmp_path, capsys):
    for name, text in (("valid", '{"mcpServers": {"a": {}}}'), ("no_servers", "{}"), ("absent", None)):
        root = tmp_path / name
        if text is not None:
            _write(root, ".mcp.json", text)
        _write(root, "src/lib.c", "int f(void);\n")
        base = _commit_all(root, "code")
        _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
        _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(MANIFEST))
        _task(root, "fix_one", base)
        _commit_all(root, "evals")
        _run_cli(["repo", "validate", f"{root}@HEAD", "--manifest", "dev/evals/repo-eval.yaml", "--json"])
        assert json.loads(capsys.readouterr().out)["ok"] is True, name
