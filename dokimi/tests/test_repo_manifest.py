# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for repositories as evaluation subjects: repo_manifest.v1, sources, repo CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos import (
    GitTree,
    ManifestError,
    RepoSource,
    SourceError,
    WorkTree,
    check_repo,
    load_manifest,
    match_glob,
    open_source,
    parse_manifest,
)
from dokimi.repos.source import cache_path
from dokimi.subjects import load_subject
from dokimi.subjects.loader import awareness_arm_specs

SHA = "0123456789abcdef0123456789abcdef01234567"

MANIFEST = {
    "schema": "repo_manifest.v1",
    "id": "toy",
    "source": {"url": "https://example.invalid/toy"},
    "eval_dir": "dev/evals",
    "guidance": {"globs": ["**/AGENTS.md", ".github/instructions/*.md"], "also_scan": ["CONTRIBUTING.md"]},
    "image": {
        "dockerfile": "dev/evals/Dockerfile",
        "caches": [{"name": "build-cache", "path": "/cache/build"}],
        "env": {"OUT_BASE": "{run_dir}/ob"},
        "network": "none",
    },
    "entry_points": {
        "grade": "python3 dev/evals/run.py grade --contract {run_dir}",
        "oracle": "python3 dev/evals/run.py oracle --contract {run_dir}",
    },
    "tasks": "dev/evals/tasks",
    "build_systems": ["make", "pytest"],
}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _task(root: Path, task_id: str, **extra) -> None:
    body = {"id": task_id, "family": "fix", "base": SHA, "grader": "tests",
            "prompt": "Fix the bug.", **extra}
    _write(root, f"dev/evals/tasks/{task_id}/task.json", json.dumps(body))


def _repo(tmp_path: Path, manifest: dict | None = None) -> Path:
    root = tmp_path / "toy"
    _write(root, "AGENTS.md", "# guidance\n")
    _write(root, "src/AGENTS.md", "# sub guidance\n")
    _write(root, ".github/instructions/c.md", "rules\n")
    _write(root, "CONTRIBUTING.md", "contrib\n")
    _write(root, "src/lib.c", "int f(void) { return 1; }\n")
    _write(root, "dev/evals/Dockerfile", "FROM scratch\n")
    _write(root, "dev/evals/repo-eval.yaml", yaml.safe_dump(manifest or MANIFEST))
    _task(root, "fix_one", setup_patch="bug.patch", gate=["strict_pass"])
    _write(root, "dev/evals/tasks/fix_one/bug.patch", "--- a\n+++ b\n")
    _task(root, "review_two", family="review", review_patch="change.patch")
    _write(root, "dev/evals/tasks/review_two/change.patch", "From x\n")
    return root


def _git_commit(root: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return git("rev-parse", "HEAD")


# --- manifest ----------------------------------------------------------------


def test_valid_repo_passes_and_reports_tasks_and_guidance(tmp_path):
    root = _repo(tmp_path)
    tree = WorkTree(root)
    manifest = load_manifest(tree, "dev/evals/repo-eval.yaml")
    report = check_repo(tree, manifest)

    assert report.ok, report.errors
    assert report.warnings == []
    assert sorted(t.id for t in report.tasks) == ["fix_one", "review_two"]
    assert {t.id: t.gate for t in report.tasks} == {"fix_one": ("strict_pass",), "review_two": ("pass",)}
    assert report.guidance_files == ["AGENTS.md", "src/AGENTS.md", ".github/instructions/c.md"]
    assert manifest.image is not None and manifest.image.network == "none"
    assert report.to_dict()["tasks"]["by_family"] == {"fix": 1, "review": 1}


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda m: m.update(schema="repo_manifest.v2"), "schema"),
        (lambda m: m.update(surprise=1), "surprise"),
        (lambda m: m.pop("entry_points"), "entry_points"),
        (lambda m: m.update(process_metrics={"bazel": "bazel("}), "process_metrics/bazel: not a valid regex"),
        (lambda m: m.update(process_metrics={"icpx": {"regex": "icpx", "match": "stderr"}}), "match"),
        (lambda m: m.update(process_metrics={"bad name": "x"}), "process_metrics"),
        (lambda m: m.update(process_metrics={"icpx": {"regex": "icpx", "mach": "output"}}), "mach"),
        (lambda m: m.update(id="Bad Id"), "id"),
        (lambda m: m.update(build_systems=["scons"]), "build_systems"),
        (lambda m: m["image"].update(network="host"), "network"),
        (lambda m: m["image"]["caches"].append({"name": "c", "path": "relative"}), "caches"),
        (lambda m: m.update(eval_dir="../elsewhere"), "'..'"),
        (lambda m: m.update(eval_dir="./"), "subdirectory"),
        (lambda m: m.update(tasks="/abs/tasks"), "relative"),
        (lambda m: m["guidance"]["globs"].append("..\\x"), "separators"),
        (lambda m: m["entry_points"].update(grade="run {workspace}"), "unknown placeholder"),
        (lambda m: m["image"]["env"].update(X="{home}"), "unknown placeholder"),
        (lambda m: m["image"]["caches"].append({"name": "build-cache", "path": "/x"}), "duplicate"),
        (lambda m: m["entry_points"].update(static="check --src {source_dir}"), "only for grade and oracle"),
        (lambda m: m["image"]["env"].update(SRC="{source_dir}"), "only for grade and oracle"),
        (lambda m: (m.pop("source"), m["entry_points"].update(grade="g {source_dir}")), "source/url"),
    ],
)
def test_malformed_manifest_is_refused(mutate, needle):
    raw = json.loads(json.dumps(MANIFEST))
    mutate(raw)
    with pytest.raises(ManifestError) as exc:
        parse_manifest(raw)
    assert needle in str(exc.value)


def test_missing_and_non_yaml_manifest(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(WorkTree(root))  # default .agent-eval/ path is absent
    _write(root, "dev/evals/repo-eval.yaml", "a: [unclosed\n")
    with pytest.raises(ManifestError, match="not valid YAML"):
        load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml")


@pytest.mark.parametrize(
    "task_extra, needle",
    [
        ({"id": "other"}, "does not equal the directory name"),
        ({"prompt": ""}, "'prompt'"),
        ({"setup_patch": "missing.patch"}, "patch file does not exist"),
        ({"setup_patch": "../../x.patch"}, "'..'"),
        ({"setup_patch": "a", "setup_patches": ["a"]}, "not both"),
        ({"review_patch": 3}, "review_patch must be a path"),
        ({"gate": []}, "gate"),
        ({"gate": [{"metric": "recall"}]}, "gate"),
        ({"base": "HEAD"}, "must be a commit SHA"),
        ({"base": "main"}, "must be a commit SHA"),
        ({"base": "v1.0^"}, "must be a commit SHA"),
        ({"score": {"metric": "recall"}}, "score must be"),
        ({"score": {"metric": "recall", "max": True}}, "score must be"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"budget_usd": True}, "budget_usd"),
        ({"budget_usd": float("nan")}, "budget_usd"),  # JSON NaN passes `<= 0`
        ({"timeout_s": float("inf")}, "timeout_s"),
        ({"egress": "pypi.org"}, "egress must be a list"),
        ({"egress": ["https://pypi.org/simple"]}, "egress must be a list"),
        ({"egress": ["*"]}, "egress must be a list"),
        ({"egress": ["**"]}, "egress must be a list"),
        ({"egress": ["*.org"]}, "egress must be a list"),
        ({"egress": ["pypi.*"]}, "egress must be a list"),
        ({"egress": ["py*.org"]}, "egress must be a list"),
        ({"expected_skill": "skills/lint"}, "expected_skill must be a skill name"),  # a path never matches usage
        ({"expected_skill": 3}, "expected_skill must be a skill name"),
    ],
)
def test_bad_task_is_reported(tmp_path, task_extra, needle):
    root = _repo(tmp_path)
    _task(root, "bad", **task_extra)
    report = check_repo(WorkTree(root), load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml"))
    assert not report.ok
    assert any("tasks/bad/task.json" in e and needle in e for e in report.errors), report.errors
    assert "bad" not in [t.id for t in report.tasks]


@pytest.mark.parametrize("task_id", ["my task", "tab\tid", "a,b"])
def test_task_id_with_whitespace_or_comma_is_refused(tmp_path, task_id):
    # the workflow splits its tasks, smoke-tasks and exclude-tasks inputs on whitespace and commas,
    # so such an id could never be selected
    root = _repo(tmp_path)
    _task(root, task_id)
    report = check_repo(WorkTree(root), load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml"))
    assert not report.ok
    assert any(f"task id {task_id!r} contains whitespace or a comma" in e for e in report.errors), report.errors
    assert task_id not in [t.id for t in report.tasks]


def test_task_gate_score_and_short_base(tmp_path):
    root = _repo(tmp_path)
    _task(root, "rev", family="review", base="cf0c0a0b^", gate=[{"metric": "recall", "min": 0.75}, "format_ok"],
          score={"metric": "recall", "max": 1.0})
    report = check_repo(WorkTree(root), load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml"))
    assert report.ok, report.errors
    task = next(t for t in report.tasks if t.id == "rev")
    assert task.gate == ("recall>=0.75", "format_ok") and task.score == {"metric": "recall", "max": 1.0}
    assert report.warnings == ["dev/evals/tasks/rev/task.json: base 'cf0c0a0b^': SHA 'cf0c0a0b' has "
                               "8 characters; use all 40 (the trailing '^' is fine)"]


def test_eval_dir_and_source_dir_placeholders_are_allowed():
    raw = json.loads(json.dumps(MANIFEST))
    raw["entry_points"]["grade"] = "python3 {eval_dir}/run.py grade --contract {run_dir} --src {source_dir}"
    raw["entry_points"]["oracle"] = "python3 {eval_dir}/run.py oracle --src {source_dir}"
    assert "{source_dir}" in parse_manifest(raw).entry_points["grade"]


def test_task_egress_is_kept(tmp_path):
    root = _repo(tmp_path)
    _task(root, "build_make", family="build", egress=["pypi.org", "*.pythonhosted.org:443"])
    report = check_repo(WorkTree(root), load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml"))
    assert report.ok, report.errors
    task = next(t for t in report.tasks if t.id == "build_make")
    assert task.egress == ("pypi.org", "*.pythonhosted.org:443")


def test_tree_level_problems(tmp_path):
    raw = json.loads(json.dumps(MANIFEST))
    raw["guidance"]["globs"].append("**/CLAUDE.md")
    raw["explore_tasks"] = "dev/evals/explore"
    root = _repo(tmp_path, raw)
    (root / "dev/evals/Dockerfile").unlink()
    _write(root, "dev/evals/AGENTS.md", "eval-only guidance\n")
    report = check_repo(WorkTree(root), load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml"))

    assert any("image/dockerfile" in e for e in report.errors)
    assert any("explore_tasks" in e for e in report.errors)
    assert any("matches no file: **/CLAUDE.md" in w for w in report.warnings)
    assert any("inside eval_dir" in w and "dev/evals/AGENTS.md" in w for w in report.warnings)


def test_tasks_outside_eval_dir_is_an_error(tmp_path):
    raw = json.loads(json.dumps(MANIFEST))
    raw["tasks"] = "tasks"
    root = _repo(tmp_path, raw)
    _write(root, "tasks/t1/task.json", json.dumps(
        {"id": "t1", "family": "fix", "base": SHA, "grader": "g", "prompt": "p"}))
    report = check_repo(WorkTree(root), load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml"))
    assert any("outside eval_dir" in e for e in report.errors)


def test_explore_tasks_outside_eval_dir_is_an_error(tmp_path):
    raw = json.loads(json.dumps(MANIFEST))
    raw["explore_tasks"] = "docs/explore"
    root = _repo(tmp_path, raw)
    _write(root, "docs/explore/t.yaml", "references: [src/lib.c]\n")
    report = check_repo(WorkTree(root), load_manifest(WorkTree(root), "dev/evals/repo-eval.yaml"))
    assert any("explore_tasks" in e and "outside eval_dir" in e for e in report.errors), report.errors


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("**/AGENTS.md", "AGENTS.md", True),
        ("**/AGENTS.md", "a/b/AGENTS.md", True),
        ("**/AGENTS.md", "a/NOTAGENTS.md", False),
        (".github/instructions/*.md", ".github/instructions/c.md", True),
        (".github/instructions/*.md", ".github/instructions/x/c.md", False),
        ("docs/**", "docs/a/b.md", True),
        ("CLAUDE.?d", "CLAUDE.md", True),
    ],
)
def test_match_glob(pattern, path, expected):
    assert match_glob(pattern, path) is expected


# --- sources -----------------------------------------------------------------


@pytest.mark.parametrize(
    "spec, location, rev, remote",
    [
        ("https://github.com/o/r@a492e703", "https://github.com/o/r", "a492e703", True),
        ("https://github.com/o/r.git@a492e703^", "https://github.com/o/r.git", "a492e703^", True),
        ("git@github.com:o/r.git@v1.2", "git@github.com:o/r.git", "v1.2", True),
    ],
)
def test_source_parse_remote(spec, location, rev, remote):
    src = RepoSource.parse(spec)
    assert (src.location, src.rev, src.remote) == (location, rev, remote)


@pytest.mark.parametrize(
    "spec, needle",
    [
        ("https://github.com/o/r", "pinned"),
        ("git@github.com:o/r.git", "pinned"),
        ("http://github.com/o/r@abc", "https://"),
        ("--upload-pack=x", "invalid"),
        ("/definitely/not/here", "not a directory"),
    ],
)
def test_source_parse_refuses(spec, needle):
    with pytest.raises(SourceError, match=needle):
        RepoSource.parse(spec)


def test_git_tree_reads_a_commit_not_the_working_tree(tmp_path):
    root = _repo(tmp_path)
    sha = _git_commit(root)
    _task(root, "uncommitted")  # only in the working tree

    tree = open_source(RepoSource.parse(f"{root}@HEAD"))
    assert isinstance(tree, GitTree) and tree.sha == sha
    assert tree.is_dir("dev/evals/tasks") and not tree.is_dir("dev/evals/tasks/uncommitted")
    report = check_repo(tree, load_manifest(tree, "dev/evals/repo-eval.yaml"))
    assert report.ok, report.errors
    assert sorted(t.id for t in report.tasks) == ["fix_one", "review_two"]
    assert "uncommitted" in [t.id for t in check_repo(WorkTree(root), report.manifest).tasks]


def test_remote_source_is_cloned_once_into_the_cache(tmp_path):
    root = _repo(tmp_path)
    sha = _git_commit(root)
    url = root.as_uri()
    cache = tmp_path / "cache"

    tree = open_source(RepoSource.parse(f"{url}@{sha}"), cache)
    assert isinstance(tree, GitTree) and tree.sha == sha
    assert tree.git_dir == cache_path(url, cache)
    assert check_repo(tree, load_manifest(tree, "dev/evals/repo-eval.yaml")).ok

    # A later commit upstream is fetched into the same clone on demand.
    _write(root, "NEW.md", "x\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "second"], check=True)
    new_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
    again = open_source(RepoSource.parse(f"{url}@{new_sha}"), cache)
    assert isinstance(again, GitTree) and again.git_dir == tree.git_dir
    assert again.is_file("NEW.md") and not tree.is_file("NEW.md")
    parent = open_source(RepoSource.parse(f"{url}@{new_sha}^"), cache)
    assert isinstance(parent, GitTree) and parent.sha == sha

    with pytest.raises(SourceError):
        open_source(RepoSource.parse(f"{url}@{'0' * 40}"), cache)


def test_work_tree_refuses_escaping_paths(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(SourceError, match="escapes"):
        WorkTree(root).read_text("../outside")


def _commit_all(root: Path, msg: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", msg], check=True)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_a_remote_clone_that_needed_a_prompt_says_which_and_what_to_do(tmp_path, monkeypatch):
    from dokimi import process
    from dokimi.repos import source

    from test_process import GIT_PROMPT_FAILURES

    stderr, line, hint = GIT_PROMPT_FAILURES[0]
    failed = process.reply(stderr=stderr, returncode=128)
    monkeypatch.setattr(process, "git", lambda *args, **kwargs: failed)
    with pytest.raises(SourceError) as err:
        source.ensure_clone("git@github.com:o/r.git", tmp_path)
    assert str(err.value).startswith(f"git clone failed: {line} (hint: ") and hint in str(err.value)


def test_remote_branch_is_refreshed_not_served_from_the_clone(tmp_path):
    root = _repo(tmp_path)
    _git_commit(root)
    branch = subprocess.run(["git", "-C", str(root), "branch", "--show-current"], check=True,
                            capture_output=True, text=True).stdout.strip()
    spec = f"{root.as_uri()}@{branch}"
    open_source(RepoSource.parse(spec), tmp_path / "cache")
    _write(root, "NEW.md", "x\n")
    new_sha = _commit_all(root, "second")
    tree = open_source(RepoSource.parse(spec), tmp_path / "cache")
    assert isinstance(tree, GitTree) and tree.sha == new_sha
    for tag in ("v1.0", "20250101"):  # cut after the clone; the second looks like a short SHA
        subprocess.run(["git", "-C", str(root), "tag", tag], check=True)
        tree = open_source(RepoSource.parse(f"{root.as_uri()}@{tag}"), tmp_path / "cache")
        assert isinstance(tree, GitTree) and tree.sha == new_sha


def test_git_tree_reads_utf8_on_any_locale(tmp_path):
    root = _repo(tmp_path)
    _write(root, "dev/evals/tasks/uni/task.json", json.dumps(
        {"id": "uni", "family": "fix", "base": SHA, "grader": "tests",
         "prompt": "Fix Nováček’s bug"}, ensure_ascii=False))
    sha = _git_commit(root)
    tree = open_source(RepoSource.parse(f"{root}@{sha}"))
    assert json.loads(tree.read_text("dev/evals/tasks/uni/task.json"))["prompt"] == "Fix Nováček’s bug"


def test_pinned_local_source_must_be_the_repository_root(tmp_path):
    root = _repo(tmp_path)
    _git_commit(root)
    with pytest.raises(SourceError, match="inside a repository"):
        open_source(RepoSource.parse(f"{root / 'dev'}@HEAD"))


# --- CLI and subject kind ----------------------------------------------------


def _run_cli(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_repo_validate_cli(tmp_path, capsys):
    root = _repo(tmp_path)
    _run_cli(["repo", "validate", str(root), "--manifest", "dev/evals/repo-eval.yaml", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["tasks"]["count"] == 2

    _task(root, "bad", id="other")
    with pytest.raises(SystemExit) as exc:
        _run_cli(["repo", "validate", str(root), "--manifest", "dev/evals/repo-eval.yaml"])
    assert exc.value.code == 1
    assert "does not equal the directory name" in capsys.readouterr().err


def test_repo_validate_fail_on_warning(tmp_path, capsys):
    guidance = {**MANIFEST["guidance"], "also_scan": ["no-such-file-*.md"]}
    root = _repo(tmp_path, {**MANIFEST, "guidance": guidance})
    argv = ["repo", "validate", str(root), "--manifest", "dev/evals/repo-eval.yaml"]
    _run_cli(argv)  # a warning alone still passes by default
    assert "warning: guidance/also_scan matches no file" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        _run_cli([*argv, "--fail-on-warning"])
    assert exc.value.code == 1
    assert "warning:" in capsys.readouterr().out


def test_repo_validate_out_writes_the_report(tmp_path, capsys):
    root = _repo(tmp_path)
    dest = tmp_path / "validate.json"
    argv = ["repo", "validate", str(root), "--manifest", "dev/evals/repo-eval.yaml", "--out", str(dest)]
    _run_cli(argv)
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written["ok"] is True and written["tasks"]["count"] == 2
    assert "tasks:" in capsys.readouterr().out  # the summary still prints

    _run_cli([*argv, "--json"])
    assert json.loads(capsys.readouterr().out) == written

    with pytest.raises(SystemExit) as exc:
        _run_cli([*argv[:-1], str(tmp_path / "no" / "dir" / "v.json")])
    assert exc.value.code == 1
    assert "cannot write" in capsys.readouterr().err


def test_repo_fetch_cli(tmp_path, capsys):
    root = _repo(tmp_path)
    sha = _git_commit(root)
    _run_cli(["repo", "fetch", f"{root.as_uri()}@HEAD", "--cache-dir", str(tmp_path / "c")])
    assert capsys.readouterr().out.split("\t")[0] == sha
    with pytest.raises(SystemExit):
        _run_cli(["repo", "fetch", str(root)])


def test_repo_fetch_cli_json(tmp_path, capsys, monkeypatch):
    from dokimi.repos.source import GitTree

    sha = "0123456789abcdef0123456789abcdef01234567"
    git_dir = tmp_path / "bare.git"
    monkeypatch.setattr("dokimi.repos.open_source", lambda source, cache: GitTree(git_dir, sha))
    _run_cli(["repo", "fetch", "https://example.invalid/r.git@main", "--json"])
    assert json.loads(capsys.readouterr().out) == {"sha": sha, "git_dir": str(git_dir)}
    _run_cli(["repo", "fetch", "https://example.invalid/r.git@main"])
    assert capsys.readouterr().out == f"{sha}\t{git_dir}\n"


def test_repo_subject_kind(tmp_path):
    path = tmp_path / "subject.toml"
    path.write_text(
        '[subject]\nid = "onedal"\nkind = "repo"\n'
        'ref = "https://github.com/uxlfoundation/oneDAL@main"\n'
        'manifest = "dev/agent_evals/repo-eval.yaml"\n\n[suite]\n',
        encoding="utf-8",
    )
    descriptor = load_subject(path)
    assert descriptor.subject.kind == "repo"
    assert descriptor.subject.manifest == "dev/agent_evals/repo-eval.yaml"
    assert descriptor.suite.questions == ()
    with pytest.raises(ValueError, match="no prompt arm"):
        awareness_arm_specs(descriptor)

    # The tasks come from the manifest, so the [suite] table may be omitted.
    path.write_text('[subject]\nid = "onedal"\nkind = "repo"\nref = "/src/oneDAL"\n', encoding="utf-8")
    assert load_subject(path).suite.tasks == ()


def test_manifest_key_and_repo_members_are_refused(tmp_path):
    path = tmp_path / "subject.toml"
    path.write_text(
        '[subject]\nid = "s"\nkind = "skill"\nref = "x"\nmanifest = "m.yaml"\n\n'
        '[suite]\nproducts = ["p"]\nquestions = ["q.json"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="only valid for kind='repo'"):
        load_subject(path)
    path.write_text(
        '[subject]\nid = "b"\nkind = "bundle"\n'
        '[[subject.members]]\nkind = "repo"\nref = "x"\n\n'
        '[suite]\nproducts = ["p"]\nquestions = ["q.json"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="members\\[0\\].kind is invalid"):
        load_subject(path)



@pytest.mark.parametrize("value", [
    "{run_dir}//tmp",             # the first path part is "/": run_dir / "/" is the host root
    "{run_dir}/agent-git/x",      # the directory _set_git_config_aside moves the real config into
    "{run_dir}/Agent-Git",        # the same entry on a host that folds case
    "{run_dir}/grade-home",
    "{run_dir}/result.json",
    "{run_dir}/../x",
    "{run_dir}/ob/../../x",
    "{run_dir}/ob//x",
    "{run_dir}/.hidden",
    "{run_dir}/ob.",
    "{run_dir}/a\b",
    "{run_dir}/a\x00b",
    "{run_dir}/ob\n",
    "{run_dir}/",
])
def test_scratch_dir_that_is_not_a_plain_name_is_refused(value):
    raw = json.loads(json.dumps(MANIFEST))
    raw["image"]["env"]["OUT_BASE"] = value
    with pytest.raises(ManifestError) as exc:
        parse_manifest(raw)
    message = str(exc.value)
    assert "image/env/OUT_BASE" in message and ("plain name" in message or "runner creates" in message)


@pytest.mark.parametrize("value", ["{run_dir}/ob", "{run_dir}/ob/", "{run_dir}/out-1/sub.d", "{run_dir}/home/.cache",
                                   "{run_dir}", "/srv/x", "plain"])
def test_scratch_dir_that_is_a_plain_name_or_outside_run_dir_is_accepted(value):
    raw = json.loads(json.dumps(MANIFEST))
    raw["image"]["env"]["OUT_BASE"] = value
    assert parse_manifest(raw).image.env["OUT_BASE"] == value


def test_reserved_run_entries_cover_what_the_runner_writes():
    from dokimi.repos.grading import AGENT_GIT, GRADE_HOME, HOST_FILES
    from dokimi.repos.manifest import RESERVED_RUN_ENTRIES

    written = {AGENT_GIT, GRADE_HOME, *HOST_FILES, "grade.json", "agent.stdout", "agent.stderr",
               "oracle.log", "oracle.err", "isolation.log", "isolation.err"}
    assert written <= RESERVED_RUN_ENTRIES


# --- guidance-only manifests --------------------------------------------------

GUIDANCE_ONLY = {"schema": "repo_manifest.v1", "id": "toy",
                 "guidance": {"globs": ["**/AGENTS.md"], "also_scan": ["CONTRIBUTING.md"]}}


def test_a_guidance_only_manifest_checks_the_guidance_and_says_what_it_cannot_do(tmp_path):
    manifest = parse_manifest(json.loads(json.dumps(GUIDANCE_ONLY)))
    assert not manifest.runnable and manifest.eval_dir is None and manifest.tasks is None
    report = check_repo(WorkTree(_repo(tmp_path)), manifest)
    assert report.ok, report.errors
    assert report.guidance_files == ["AGENTS.md", "src/AGENTS.md"]
    assert report.tasks == []
    assert any("guidance-only" in note for note in report.notes)


@pytest.mark.parametrize("extra", [
    {"eval_dir": "dev/evals"},
    {"tasks": "dev/evals/tasks"},
    {"entry_points": {"grade": "true"}},
    {"image": {"dockerfile": "Dockerfile"}},
    {"explore_tasks": "dev/evals/explore"},
])
def test_half_a_runnable_manifest_is_refused(extra):
    with pytest.raises(ManifestError, match="without"):
        parse_manifest({**json.loads(json.dumps(GUIDANCE_ONLY)), **extra})


def test_running_tasks_refuses_a_guidance_only_manifest():
    from dokimi.repos.manifest import require_runnable

    with pytest.raises(ManifestError, match="running tasks needs eval_dir"):
        require_runnable(parse_manifest(json.loads(json.dumps(GUIDANCE_ONLY))))
    require_runnable(parse_manifest(json.loads(json.dumps(MANIFEST))))


def test_scan_without_a_manifest_checks_the_default_guidance(tmp_path, capsys):
    root = tmp_path / "plain"
    _write(root, "AGENTS.md", "See `src/missing.py`.\n")
    _write(root, "src/CLAUDE.md", "# scoped\n")
    args = build_parser().parse_args(["repo", "scan", str(root), "--json"])
    args.func(args)
    captured = capsys.readouterr()
    assert "no .agent-eval/repo-eval.yaml" in captured.err
    payload = json.loads(captured.out)
    assert payload["repo"]["id"] == "plain"
    assert set(payload["files"]) == {"AGENTS.md", "src/CLAUDE.md"}
    assert payload["counts"]["fail"] == 1      # src/missing.py


def test_validate_still_needs_a_manifest(tmp_path):
    args = build_parser().parse_args(["repo", "validate", str(_repo(tmp_path))])
    with pytest.raises(SystemExit):
        args.func(args)


@pytest.mark.parametrize("label, expected", [
    ("/home/u/repos/abicheck", "abicheck"),
    ("/home/u/oneDAL@0123456789ab", "onedal"),
    ("https://github.com/intel/dokimi.git", "dokimi"),
    ("git@github.com:o/My Repo", "my-repo"),
    ("/", "repo"),
])
def test_the_default_manifest_id_is_a_valid_id(label, expected):
    from dokimi.commands.repo import _manifest_id

    assert _manifest_id(label) == expected


def test_a_mistyped_manifest_path_is_an_error_not_a_default_scan(tmp_path, capsys):
    root = tmp_path / "plain"
    _write(root, "AGENTS.md", "# guidance\n")
    args = build_parser().parse_args(["repo", "scan", str(root), "--manifest", ".agent-eval/repo-evel.yaml"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert "manifest not found" in capsys.readouterr().err


@pytest.mark.parametrize("rev", ["origin/main", "refs/heads/x", "HEAD~1", "v1.2", "a492e703^"])
def test_source_parse_takes_a_rev_with_a_slash(tmp_path, rev):
    src = RepoSource.parse(f"{tmp_path}@{rev}")
    assert (src.location, src.rev, src.remote) == (str(tmp_path), rev, False)
    remote = RepoSource.parse(f"git@github.com:o/r.git@{rev}")
    assert (remote.location, remote.rev) == ("git@github.com:o/r.git", rev)


@pytest.mark.parametrize("rev", ["a..b", "origin//main", "origin/", "-x"])
def test_source_parse_leaves_a_non_rev_suffix_in_the_path(tmp_path, rev):
    (tmp_path / f"d@{rev}").mkdir(parents=True)
    src = RepoSource.parse(f"{tmp_path}/d@{rev}")
    assert src.rev is None and src.location == f"{tmp_path}/d@{rev}"


@pytest.mark.parametrize("url", [
    "ssh://git@github.com/o/r.git", "https://user@github.com/o/r.git",
    "file:///home/a@corp.com/proj", "file:///tmp/a@b/c", "https://github.com/o@x/r",
    "git@github.com/o/r", "file://a@main", "https://h/o/r@/abs"])
def test_an_unpinned_remote_url_with_a_user_is_not_split_at_its_at_sign(url):
    with pytest.raises(SourceError, match="must be pinned"):
        RepoSource.parse(url)


@pytest.mark.parametrize("url", ["ssh://git@github.com/o/r.git", "https://user@github.com/o/r.git"])
def test_a_pinned_remote_url_with_a_user_splits_at_the_last_at_sign(url):
    src = RepoSource.parse(f"{url}@origin/main")
    assert (src.location, src.rev, src.remote) == (url, "origin/main", True)


@pytest.mark.parametrize("spec, location, rev", [
    ("ssh://git@host/o/r@origin/main", "ssh://git@host/o/r", "origin/main"),
    ("git@host:o/r@sha1234", "git@host:o/r", "sha1234"),
    ("git@host:o/r@refs/heads/x", "git@host:o/r", "refs/heads/x"),
    ("ssh://git@host/o/r@upstream/main", "ssh://git@host/o/r", "upstream/main"),
    ("ssh://git@host/o/r@remotes/origin/main", "ssh://git@host/o/r", "remotes/origin/main"),
    ("ssh://git@host/o/r@heads/x", "ssh://git@host/o/r", "heads/x"),
    ("ssh://git@host/o/r@tags/v1", "ssh://git@host/o/r", "tags/v1"),
    ("file:///home/a@corp.com/proj@abc1234", "file:///home/a@corp.com/proj", "abc1234"),
    ("file:///tmp/a@b/c@v1", "file:///tmp/a@b/c", "v1"),
])
def test_a_pinned_remote_spec_with_an_at_sign_in_its_path_splits_at_the_last_one(spec, location, rev):
    src = RepoSource.parse(spec)
    assert (src.location, src.rev, src.remote) == (location, rev, True)


@pytest.mark.parametrize("spec", ["git@host:o/r@sha1234", "file:/tmp/x@main"])
def test_a_remote_spec_is_split_even_if_a_directory_of_that_name_exists(tmp_path, monkeypatch, spec):
    (tmp_path / spec).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert RepoSource.parse(spec if spec.startswith("git@") else "file:///tmp/x@main").rev


def test_a_local_rev_does_not_start_with_a_slash(tmp_path):
    (tmp_path / "d").mkdir()
    with pytest.raises(SourceError, match="not a directory"):
        RepoSource.parse(f"{tmp_path}/d@/x")


@pytest.mark.parametrize("rev", ["a..b", "x//y", "y/", "-x"])
def test_resolve_rev_refuses_what_is_no_revision(tmp_path, rev):
    from dokimi.repos.source import resolve_rev

    root = _repo(tmp_path)
    _git_commit(root)
    with pytest.raises(SourceError, match="invalid revision"):
        resolve_rev(root, rev)


def test_resolve_rev_takes_a_branch_with_a_slash(tmp_path):
    from dokimi.repos.source import resolve_rev

    root = _repo(tmp_path)
    sha = _git_commit(root)
    subprocess.run(["git", "-C", str(root), "branch", "origin/main"], check=True)
    assert resolve_rev(root, "origin/main") == sha


@pytest.mark.parametrize("sub", ["ws/job@2/repo", "home/alice@corp.com/proj", "gomod/r@v1.2.3/skills",
                                 "node_modules/@types/node", "d@HEAD"])
def test_an_existing_directory_with_an_at_sign_is_read_whole(tmp_path, sub):
    (tmp_path / sub).mkdir(parents=True)
    src = RepoSource.parse(f"{tmp_path}/{sub}")
    assert (src.location, src.rev) == (f"{tmp_path}/{sub}", None)
    pinned = RepoSource.parse(f"{tmp_path}/{sub}@origin/main")
    assert (pinned.location, pinned.rev) == (f"{tmp_path}/{sub}", "origin/main")
