# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for checks across task bases (repos/bases.py) and repo_static.v1 (repos/static.py)."""

from __future__ import annotations

import json
import subprocess
import tempfile
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
    out = json.loads(capsys.readouterr().out)["data"]
    assert out["ok"] is True, out["errors"]
    assert out["warnings"] == []  # .github/instructions/*.md still matches at a task base
    assert out["guidance_files"] == ["AGENTS.md"]
    assert out["guidance_by_base"] == {old: 2, f"{old}^": 0, bare: 0}
    assert len(out["notes"]) == 1 and out["notes"][0].endswith(": fix_bare, fix_older")
    assert out["notes"][0].startswith("no file the guidance globs match and no AGENTS.<agent>.md overlay at the base "
                                      "of 2 task(s) or in their setup patches; ")
    # rev: and patch: bring their own files, so the note must not cover them (#481)
    assert "guidance:none, raw and bridge give the same workspace" in out["notes"][0]
    assert "(guidance:rev:, patch: and +skill: parts bring their own files)" in out["notes"][0]

    _task(root, "fix_gone", "f" * 40)
    _commit_all(root, "unknown base")
    with pytest.raises(SystemExit):
        _run_cli(argv)
    out = json.loads(capsys.readouterr().out)["data"]
    assert any("fix_gone" in e and "does not resolve" in e for e in out["errors"]), out["errors"]


def _workspace_files(ws: Path) -> list[str]:
    return sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*") if p.is_file() and ".git" not in p.parts)


def test_bases_note_leaves_out_a_base_whose_overlay_the_globs_miss(tmp_path, capsys):
    """guidance:none removes an overlay and guidance:overlay keeps it whatever the globs match, so a base with one is
    no bare base: there none differs from raw and the overlay arm prepares. Setup patches apply before the arm, so
    one that adds an overlay does the same at a bare base (PR #485 review)."""
    from dokimi.repos.workspace import PrepError, export_eval_dir, load_tasks, prepare_workspace, source_git_dir

    root = tmp_path / "toy"
    _write(root, "AGENTS.md", "# guidance\n")
    _write(root, "dev/evals/AGENTS.codex.md", "# under eval_dir: step 2 deletes it\n")
    plain = _commit_all(root, "AGENTS.md only")
    _write(root, "AGENTS.claude.md", "# for claude\n")
    overlaid = _commit_all(root, "an overlay")
    raw = json.loads(json.dumps(MANIFEST))
    raw["guidance"] = {"globs": ["CLAUDE.md"]}
    _write(root, "CLAUDE.md", "# guidance at the eval tree\n")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(raw))
    _task(root, "fix_plain", plain)
    _task(root, "fix_overlaid", overlaid)
    patch = "--- /dev/null\n+++ b/AGENTS.claude.md\n@@ -0,0 +1 @@\n+# for claude, from a setup patch\n"
    _write(root, "dev/evals/tasks/fix_patched/setup.patch", patch)
    _write(root, "dev/evals/tasks/fix_patched/task.json", json.dumps(
        {"id": "fix_patched", "family": "fix", "base": plain, "grader": "tests", "prompt": "Fix the bug.",
         "setup_patches": ["setup.patch"]}))
    _commit_all(root, "evals")

    _run_cli(["repo", "validate", f"{root}@HEAD", "--manifest", "dev/evals/repo-eval.yaml", "--json"])
    out = json.loads(capsys.readouterr().out)["data"]
    assert out["ok"] is True, out["errors"]
    assert out["guidance_by_base"] == {plain: 0, overlaid: 0}
    assert len(out["notes"]) == 1 and out["notes"][0].endswith("own files): fix_plain"), out["notes"]
    assert "1 task(s) or in their setup patches; there guidance:none" in out["notes"][0]

    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, "dev/evals/repo-eval.yaml")
    tasks = {t.id: t for t in load_tasks(export_eval_dir(tree, manifest, tmp_path / "content"), manifest)}
    git_dir, profile = source_git_dir(source, tree), _claude_code_profile()

    def files(task: str, arm: str) -> list[str]:
        prepare_workspace(git_dir, manifest, tasks[task], arm, tmp_path / task / arm, profile)
        return _workspace_files(tmp_path / task / arm / "workspace")

    assert files("fix_plain", "guidance:none") == files("fix_plain", "guidance:raw") == ["AGENTS.md"]
    with pytest.raises(PrepError, match="no AGENTS.claude.md to keep"):
        files("fix_plain", "guidance:overlay")
    assert files("fix_overlaid", "guidance:raw") == ["AGENTS.claude.md", "AGENTS.md"]
    assert files("fix_overlaid", "guidance:none") == ["AGENTS.md"]
    assert files("fix_overlaid", "guidance:overlay") == ["AGENTS.claude.md", "AGENTS.md"]
    assert files("fix_patched", "guidance:raw") == ["AGENTS.claude.md", "AGENTS.md"]
    assert files("fix_patched", "guidance:none") == ["AGENTS.md"]
    assert files("fix_patched", "guidance:overlay") == ["AGENTS.claude.md", "AGENTS.md"]


def test_bases_note_keeps_a_task_whose_setup_patch_writes_no_guidance(tmp_path, capsys, monkeypatch):
    """A setup patch that writes only code leaves guidance:none and raw the same workspace, so its task stays in the
    note, whatever its bytes (not UTF-8, a lone CR in an added line); one that writes a file the globs match or an
    overlay (a rename too) or is no patch leaves it out (#485)."""
    from dokimi.repos.workspace import export_eval_dir, load_tasks, prepare_workspace, source_git_dir

    root = tmp_path / "toy"
    _write(root, "src/lib.c", "int f(void);\n")
    _write(root, "src/notes.md", "# notes\n")
    bare = _commit_all(root, "code only")
    raw = json.loads(json.dumps(MANIFEST))
    raw["guidance"] = {"globs": ["CLAUDE.md"]}
    _write(root, "CLAUDE.md", "# guidance at the eval tree\n")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(raw))
    patches = {
        "fix_code": "--- /dev/null\n+++ b/src/extra.c\n@@ -0,0 +1 @@\n+int g(void);\n",
        "fix_glob": "--- /dev/null\n+++ b/CLAUDE.md\n@@ -0,0 +1 @@\n+# guidance from a setup patch\n",
        "fix_renamed": ("diff --git a/src/notes.md b/AGENTS.claude.md\nsimilarity index 100%\n"
                        "rename from src/notes.md\nrename to AGENTS.claude.md\n"),
        "fix_garbled": "not a patch\n",
        "fix_latin1": "",  # its bytes, not UTF-8, are written below
        "fix_cr": "",  # so are these, with a lone CR inside an added line
    }
    for task_id, text in patches.items():
        _write(root, f"dev/evals/tasks/{task_id}/setup.patch", text)
        _write(root, f"dev/evals/tasks/{task_id}/task.json", json.dumps(
            {"id": task_id, "family": "fix", "base": bare, "grader": "tests", "prompt": "Fix the bug.",
             "setup_patches": ["setup.patch"]}))
    (root / "dev/evals/tasks/fix_latin1/setup.patch").write_bytes(b"--- /dev/null\n+++ b/a.c\n@@ -0,0 +1 @@\n+/* caf\xe9 */\n")
    # one added line, which a CR read as a newline would turn into the header of a patch for CLAUDE.md
    (root / "dev/evals/tasks/fix_cr/setup.patch").write_bytes(
        b"--- /dev/null\n+++ b/b.c\n@@ -0,0 +1 @@\n+x\rdiff --git a/CLAUDE.md b/CLAUDE.md\nnew file mode 100644\n")
    _commit_all(root, "evals")

    with monkeypatch.context() as m:  # a temp dir in a work tree's subdirectory: git there would skip every path
        m.setattr(tempfile, "tempdir", str(root / "src"))
        _run_cli(["repo", "validate", f"{root}@HEAD", "--manifest", "dev/evals/repo-eval.yaml", "--json"])
    out = json.loads(capsys.readouterr().out)["data"]
    assert out["ok"] is True, out["errors"]
    assert out["guidance_by_base"] == {bare: 0}
    assert len(out["notes"]) == 1 and out["notes"][0].endswith("own files): fix_code, fix_cr, fix_latin1"), out["notes"]
    assert out["notes"][0].startswith("no file the guidance globs match and no AGENTS.<agent>.md overlay at the base "
                                      "of 3 task(s) or in their setup patches; there guidance:none, raw and bridge")

    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, "dev/evals/repo-eval.yaml")
    task = next(t for t in load_tasks(export_eval_dir(tree, manifest, tmp_path / "content"), manifest)
                if t.id == "fix_code")
    git_dir, profile = source_git_dir(source, tree), _claude_code_profile()
    for arm in ("guidance:none", "guidance:raw"):
        prepare_workspace(git_dir, manifest, task, arm, tmp_path / arm, profile)
        assert _workspace_files(tmp_path / arm / "workspace") == ["src/extra.c", "src/lib.c", "src/notes.md"]


def test_setup_writes_guidance_reads_a_work_tree_patch_unchanged_and_counts_a_missing_one(tmp_path):
    """On a work tree too, a code-only patch with a Latin-1 byte writes no guidance and one that is not there counts
    as writing it (#485)."""
    from dokimi.repos.bases import setup_writes_guidance

    root = tmp_path / "toy"
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(MANIFEST))
    (root / "code.patch").write_bytes(b"--- /dev/null\n+++ b/a.c\n@@ -0,0 +1 @@\n+/* caf\xe9 */\n")
    tree = WorkTree(root)
    manifest = load_manifest(tree, "dev/evals/repo-eval.yaml")

    assert setup_writes_guidance(tree, manifest, ("code.patch",)) is False
    assert setup_writes_guidance(tree, manifest, ("code.patch", "gone.patch")) is True


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
    out = json.loads(capsys.readouterr().out)["data"]
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
    out = json.loads(capsys.readouterr().out)["data"]
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
        out = json.loads(capsys.readouterr().out)["data"]
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
        assert json.loads(capsys.readouterr().out)["data"]["ok"] is True, name


def _mcp_base(root: Path, content: bytes | None) -> str:
    """Commit a base whose root `.mcp.json` holds ``content`` (none: no file); returns its sha."""
    if content is None:
        _write(root, "README.md", "no mcp\n")
    else:
        (root / ".mcp.json").parent.mkdir(parents=True, exist_ok=True)
        (root / ".mcp.json").write_bytes(content)
    return _commit_all(root, "base")


def _claude_code_profile():
    from dokimi.repos.harness_profile import load_profile
    return load_profile("claude-code")


def test_the_preflight_checks_every_task_base_not_only_the_first(tmp_path):
    from types import SimpleNamespace

    from dokimi.repos.bases import check_mcp_configs

    root = tmp_path / "r"
    good = _mcp_base(root, b'{"mcpServers": {}}')
    bad = _mcp_base(root, b"{")
    tasks = [SimpleNamespace(base=good), SimpleNamespace(base=bad)]
    profile = _claude_code_profile()
    with pytest.raises(ValueError, match=f"task base {bad}.*not valid JSON"):
        check_mcp_configs(root / ".git", tasks, profile, "all")
    check_mcp_configs(root / ".git", tasks, profile, "read")  # the file is not passed to the agent
    check_mcp_configs(root / ".git", tasks[:1], profile, "all")


def test_the_preflight_leaves_an_unresolvable_base_and_a_base_without_the_file_to_the_other_checks(tmp_path):
    from types import SimpleNamespace

    from dokimi.repos.bases import check_mcp_configs

    root = tmp_path / "r"
    none = _mcp_base(root, None)
    tasks = [SimpleNamespace(base=none), SimpleNamespace(base="0" * 40), SimpleNamespace(base="no-such-ref")]
    check_mcp_configs(root / ".git", tasks, _claude_code_profile(), "all")  # no raise


def test_a_non_utf8_mcp_json_at_a_base_is_refused_as_the_runner_refuses_it(tmp_path):
    from types import SimpleNamespace

    from dokimi.repos.bases import NOT_UTF8, check_mcp_configs, tree_mcp_error
    from dokimi.repos.source import GitTree

    root = tmp_path / "r"
    base = _mcp_base(root, b"\xff{}")
    assert tree_mcp_error(GitTree(root / ".git", base)) == NOT_UTF8
    assert tree_mcp_error(WorkTree(root)) == NOT_UTF8
    with pytest.raises(ValueError, match="not valid UTF-8"):
        check_mcp_configs(root / ".git", [SimpleNamespace(base=base)], _claude_code_profile(), "all")
