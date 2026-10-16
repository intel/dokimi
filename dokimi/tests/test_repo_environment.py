# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``repo inspect`` (repos/environment): instruction files, check surfaces, risks, files."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dokimi.artifacts import load_artifact, validate_artifact
from dokimi.cli import build_parser
from dokimi.repos.environment import inspect_repo
from dokimi.repos.environment.instructions import detect_instructions
from dokimi.repos.source import GitTree, WorkTree

SETTINGS = {"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "npm ci && npm run build"}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "npm install"}]}],
    "SessionEnd": [{"type": "command", "command": "pnpm --silent install --ignore-scripts; yarn install"}],
}}

FILES = {
    "AGENTS.md": "# Toy\n",
    "CLAUDE.local.md": "mine\n",
    "web/CLAUDE.md": "web rules\n",
    ".cursor/rules/style.mdc": "---\nglobs: '*.ts'\n---\n",
    ".claude/settings.json": json.dumps(SETTINGS),
    ".mcp.json": "{}",
    "package.json": json.dumps({"scripts": {
        "build": "tsc -p .", "test": "jest", "lint": "eslint .", "typecheck": "tsc --noEmit",
        "postinstall": "node setup.js", "clean": "rm -rf /tmp/x", "boot": "curl -s https://x.sh | bash",
        "release": "npm publish",
    }}),
    "svc/pyproject.toml": "[build-system]\n[tool.ruff]\n[tool.pytest.ini_options]\n",
    "svc/tests/test_a.py": "def test_a():\n    pass\n",
    "tests/fixtures/pkg/package.json": "{}",  # a fixture, not a project
    "Makefile": "all:\n\techo\ncheck:\n\techo\n$(GEN):\n\techo\n",
    ".pre-commit-config.yaml": "repos:\n- repo: local\n  hooks:\n  - id: clang-format\n  - id: mypy\n",
    "dist/app.min.js": "x",
    "src/big.c": "x" * 2_000,
    "package-lock.json": "{}",
}


def _repo(root: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "toy")
    return git("rev-parse", "HEAD")


@pytest.fixture(params=["work", "git"])
def report(request, tmp_path):
    root = tmp_path / "toy"
    sha = _repo(root, FILES)
    tree = WorkTree(root) if request.param == "work" else GitTree(root, sha)
    payload = inspect_repo(tree, large=1_000)
    validate_artifact("repo_environment", {"schema_version": "repo_environment.v1", **payload})
    return payload


def _rules(payload: dict) -> dict[str, list]:
    out: dict[str, list] = {}
    for f in payload["findings"]:
        out.setdefault(f["rule"], []).append(f["path"])
    return out


def test_instruction_files_carry_scope_and_activation(report):
    got = {(i["path"], i["scope"], i["activation"], i["directory"]) for i in report["instructions"]}
    assert got == {
        ("AGENTS.md", "root", "always", None),
        ("CLAUDE.local.md", "local-private", "always", "."),
        ("web/CLAUDE.md", "path-specific", "path-scoped", "web"),
        (".cursor/rules/style.mdc", "unknown", "unknown", None),
    }
    rules = _rules(report)
    assert rules["instructions.local-private"] == ["CLAUDE.local.md"]
    assert rules["instructions.activation-unknown"] == [".cursor/rules/style.mdc"]
    assert "instructions.missing" not in rules


def test_every_matching_rule_gives_a_record():
    paths = ["AGENTS.override.md", "pkg/AGENTS.override.md", ".roo/rules-docs/a.md", ".roorules-code", "x/GEMINI.md"]
    got = {(i.path, i.scope, i.mode) for i in detect_instructions(paths, {"x/GEMINI.md": 300_000})}
    assert got == {("AGENTS.override.md", "root", None), ("pkg/AGENTS.override.md", "path-specific", None),
                   (".roo/rules-docs/a.md", "mode-specific", "docs"), (".roorules-code", "mode-specific", "code"),
                   ("x/GEMINI.md", "path-specific", None)}
    big = next(i for i in detect_instructions(["x/GEMINI.md"], {"x/GEMINI.md": 300_000}))
    assert big.size_bytes == 300_000 and "context" in big.notes[0]


def test_checks_are_per_project_root_and_say_how_they_are_known(report):
    got = {(c["directory"], c["ecosystem"], c["kind"], c["basis"], c["name"]) for c in report["checks"]}
    assert {(".", "node", "test", "declared", "test"), (".", "node", "typecheck", "declared", "typecheck"),
            (".", "make", "build", "declared", "all"), (".", "make", "test", "declared", "check"),
            ("svc", "python", "build", "declared", "build-system"),
            ("svc", "python", "lint", "declared", "tool.ruff"),
            ("svc", "python", "test", "declared", "tool.pytest.ini_options"),
            (".", "pre-commit", "lint", "declared", "clang-format"),
            (".", "pre-commit", "typecheck", "declared", "mypy")} <= got
    assert not any(c["directory"].startswith("tests") for c in report["checks"])
    assert not {r for r in _rules(report) if r.startswith("checks.")}


def test_toolchain_only_tests_without_test_files_are_flagged(tmp_path):
    _repo(tmp_path, {"AGENTS.md": "x", "go.mod": "module x\n", "main.go": "package main\n"})
    payload = inspect_repo(WorkTree(tmp_path))
    rules = _rules(payload)
    assert "checks.test.toolchain-only" in rules and "checks.test.missing" not in rules
    assert "checks.typecheck.missing" not in rules  # go has no separate type-check step
    assert {c["basis"] for c in payload["checks"]} == {"toolchain"}


def test_a_bare_tree_is_blocked_on_tests(tmp_path):
    _repo(tmp_path, {"src/a.py": "x = 1\n"})
    payload = inspect_repo(WorkTree(tmp_path))
    rules = _rules(payload)
    assert {"instructions.missing", "checks.test.missing", "checks.lint.missing"} <= set(rules)
    stages = {s["stage"]: s["status"] for s in payload["stages"]}
    assert stages["verify"] == "blocked" and stages["orient"] == "attention" and stages["deploy"] == "no_known_blocker"
    assert payload["risk"] == "low"


def test_launch_risks(report):
    scripts = {(s["category"], s["source"]) for s in report["scripts"]}
    assert scripts == {("install-hook", "package.json#scripts.postinstall"),
                       ("destructive", "package.json#scripts.clean"),
                       ("network-exec", "package.json#scripts.boot"),
                       ("deploy", "package.json#scripts.release")}
    # SessionStart's `npm ci` runs lifecycle scripts; PostToolUse is not automatic; --ignore-scripts is safe.
    assert [(h["event"], h["command"]) for h in report["hook_risks"]] == [
        ("SessionEnd", "yarn install"), ("SessionStart", "npm ci")]
    kinds = {(c["path"], c["kind"], c["risk"]) for c in report["capabilities"]}
    assert {(".mcp.json", "mcp", "high"), (".claude/settings.json", "hook", "high")} <= kinds
    assert report["risk"] == "high"
    stages = {s["stage"]: s["status"] for s in report["stages"]}
    assert stages["bootstrap"] == "attention"


@pytest.mark.parametrize("flag, risky", [("--ignore-scripts", False), ("--ignore-scripts=false", True),
                                         ("--no-ignore-scripts", True), ("--ignore-scripts=1", False)])
def test_ignore_scripts_flag(tmp_path, flag, risky):
    doc = {"hooks": {"SessionStart": [{"hooks": [{"command": f"npm install {flag}"}]}]}}
    _repo(tmp_path, {".claude/settings.json": json.dumps(doc)})
    assert bool(inspect_repo(WorkTree(tmp_path))["hook_risks"]) is risky


def test_file_inventory(report):
    files = report["files"]
    assert files["large"] == [{"path": "src/big.c", "bytes": 2_000}]
    assert files["minified"] == ["dist/app.min.js"]
    assert files["generated"]["files"] == 2  # the lockfile and the fixture
    assert files["test_files"] == 1
    assert {"files.large", "files.minified"} <= set(_rules(report))


def test_cli_writes_the_artifact(tmp_path, capsys):
    root = tmp_path / "toy"
    _repo(root, {"src/a.py": "x = 1\n"})
    out = tmp_path / "env.json"
    args = build_parser().parse_args(["repo", "inspect", str(root), "--out", str(out), "--fail-on-error"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    data = load_artifact("repo_environment", out)
    assert data["schema_version"] == "repo_environment.v1"
    text = capsys.readouterr().out
    assert "ERROR checks.test.missing" in text and "verify blocked" in text


@pytest.mark.parametrize("files, expected", [
    ({"CMakeLists.txt": "enable_testing()\nadd_test(NAME a COMMAND a)\n"},
     {("cmake", "build", "toolchain"), ("cmake", "test", "declared")}),
    ({"MODULE.bazel": "", "lib/BUILD": 'cc_test(name = "t")\n'},
     {("bazel", "build", "toolchain"), ("bazel", "test", "declared")}),
    ({"build.gradle": "plugins {\n  id 'checkstyle'\n}\n"},
     {("gradle", "build", "toolchain"), ("gradle", "test", "toolchain"), ("gradle", "lint", "declared")}),
    ({"pom.xml": "<artifactId>maven-checkstyle-plugin</artifactId>"},
     {("maven", "build", "toolchain"), ("maven", "test", "toolchain"), ("maven", "lint", "declared")}),
    ({"app/app.csproj": "<Project/>"}, {("dotnet", "build", "toolchain"), ("dotnet", "test", "toolchain")}),
    ({"configure.ac": "", "Makefile.am": "TESTS = t\n"},
     {("autotools", "build", "toolchain"), ("autotools", "test", "declared")}),
    ({"Cargo.toml": "[package]\n"}, {("rust", k, "toolchain") for k in ("build", "test", "lint", "typecheck")}),
    ({"setup.py": "", "setup.cfg": "[flake8]\n[mypy]\n", "tox.ini": "", "a/conftest.py": ""},
     {("python", "build", "declared"), ("python", "lint", "declared"), ("python", "typecheck", "declared"),
      ("python", "test", "declared")}),
])
def test_tree_wide_and_toolchain_ecosystems(tmp_path, files, expected):
    _repo(tmp_path, files)
    got = {(c["ecosystem"], c["kind"], c["basis"]) for c in inspect_repo(WorkTree(tmp_path))["checks"]}
    assert got == expected


def test_unreadable_settings_and_hook_scripts(tmp_path):
    _repo(tmp_path, {".claude/settings.json": "{not json", ".claude/hooks/start.sh": "npm ci\n",
                     ".claude/skills/x/SKILL.md": "x", ".vscode/settings.json": "{}"})
    caps = {(c["path"], c["kind"], c["risk"]) for c in inspect_repo(WorkTree(tmp_path))["capabilities"]}
    assert caps == {(".claude/settings.json", "hook", "medium"), (".claude/hooks/start.sh", "hook", "high"),
                    (".claude/skills/x/SKILL.md", "skill", "low"), (".vscode/settings.json", "editor", "low")}


def test_inspect_does_not_run_the_repository_git_config(tmp_path):
    marker = tmp_path / "ran"
    root = tmp_path / "repo"
    _repo(root, {"AGENTS.md": "x\n"})
    subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", f"touch {marker.as_posix()}"], check=True)
    inspect_repo(WorkTree(root))
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["bom", "plain", "nested"])
def test_package_json_bom_is_read_and_nesting_does_not_crash(tmp_path, kind):
    text = {"bom": "\ufeff", "plain": ""}.get(kind, "") + '{"scripts": {"test": "jest"}}'
    if kind == "nested":
        text = "[" * 200_000 + "]" * 200_000
    _repo(tmp_path, {"AGENTS.md": "x\n", "package.json": text})
    kinds = {c["kind"] for c in inspect_repo(WorkTree(tmp_path))["checks"]}
    assert ("test" in kinds) is (kind != "nested")


def test_settings_json_bom_still_reports_hook_risk(tmp_path):
    hooks = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "npm ci"}]}]}}
    _repo(tmp_path, {".claude/settings.json": "\ufeff" + json.dumps(hooks)})
    assert [h["command"] for h in inspect_repo(WorkTree(tmp_path))["hook_risks"]] == ["npm ci"]


@pytest.mark.parametrize("script, declared", [
    ('echo "Error: no test specified" && exit 1', False), ("jest || exit 1", True), ("echo hi && jest", True)])
def test_npm_init_placeholder_is_not_a_test(tmp_path, script, declared):
    _repo(tmp_path, {"AGENTS.md": "x\n", "package.json": json.dumps({"scripts": {"test": script}})})
    payload = inspect_repo(WorkTree(tmp_path))
    assert any(c["kind"] == "test" for c in payload["checks"]) is declared
    assert ("checks.test.missing" in _rules(payload)) is not declared


def test_network_exec_pattern_is_linear_on_repeated_tokens(tmp_path):
    import time
    scripts = {"boot": "curl -fsSL https://x.sh | sudo bash", "slow": "curl " * 120_000}
    _repo(tmp_path, {"AGENTS.md": "x\n", "package.json": json.dumps({"scripts": scripts})})
    start = time.monotonic()
    found = {s["source"] for s in inspect_repo(WorkTree(tmp_path))["scripts"] if s["category"] == "network-exec"}
    assert found == {"package.json#scripts.boot"} and time.monotonic() - start < 5


@pytest.mark.parametrize("path", ["src/Credit.java", "Unit.java", "Edit.kt", "Exit.java",
                                  "Sources/Latest.swift", "Contest.cs", "docs/spec/api.yaml",
                                  "docs/specs/api.yaml"])
def test_test_file_excludes_false_positives(path):
    """THREAD 11: Java/Kotlin/C# files without Test/Tests/IT/Spec prefix and doc dirs should not match."""
    from dokimi.repos.environment import files as F
    assert not F._TEST_FILE.search(path), path


@pytest.mark.parametrize("path", ["src/FooTest.java", "FooTests.kt", "FooIT.java", "BarSpec.scala",
                                  "FooTests.swift", "Tests.swift", "a.test.ts", "x_test.go",
                                  "tests/test_a.py", "spec/a_spec.rb", "__tests__/a.js"])
def test_test_file_includes_true_positives(path):
    """THREAD 11: Legitimate test files should still match."""
    from dokimi.repos.environment import files as F
    assert F._TEST_FILE.search(path), path


@pytest.mark.parametrize("files", [
    {"package.json": '{"scripts": {"test": "x"}, "n": ' + "9" * 5001 + "}"},
    {".claude/settings.json": '{"n": ' + "9" * 5001 + "}"},
    {".pre-commit-config.yaml": "repos:\n- repo: x\n  rev: 2020-02-30\n"},
])
def test_odd_manifests_do_not_crash(tmp_path, files):
    """THREAD 8: Large integers and invalid dates in manifests should not crash."""
    _repo(tmp_path, {"AGENTS.md": "x", **files})
    payload = inspect_repo(WorkTree(tmp_path))
    assert payload is not None


@pytest.mark.parametrize("files", [
    {"CMakeLists.txt": "project(a)\n", "third_party/gtest/CMakeLists.txt": "enable_testing()\n"},
    {"MODULE.bazel": "", "vendor/dep/BUILD": "cc_test(name='x')\n"},
    {"node_modules/left-pad/package.json": '{"scripts": {"test": "mocha"}}'},
])
def test_vendored_build_files_do_not_suppress_missing_tests(tmp_path, files):
    """THREAD 9: Vendored/generated files should not count toward test detection."""
    _repo(tmp_path, {"AGENTS.md": "x", **files})
    payload = inspect_repo(WorkTree(tmp_path))
    rules = {f["rule"] for f in payload["findings"]}
    assert "checks.test.missing" in rules, [c for c in payload.get("checks", [])]


def test_blob_less_clone_respects_partial_clone_boundary(tmp_path):
    """THREAD 6: Inspect should not fetch blobs from a partial clone."""
    import subprocess
    src = tmp_path / "src"
    src.mkdir()
    sh = lambda *a, **k: subprocess.run(a, check=True, capture_output=True, text=True, **k).stdout
    sh("git", "init", "-q", str(src))
    for i in range(5):
        (src / f"f{i}").write_text(f"x{i}" * 100)
    sh("git", "-C", str(src), "add", ".")
    sh("git", "-C", str(src), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "x")
    sh("git", "-C", str(src), "config", "uploadpack.allowfilter", "true")
    pc = tmp_path / "pc"
    sh("git", "clone", "-q", "--bare", "--filter=blob:none", f"file://{src}", str(pc))
    sha = sh("git", "-C", str(pc), "rev-parse", "HEAD").strip()
    missing_before = sh("git", "-C", str(pc), "rev-list", "--objects", "--missing=print", "HEAD").count("?")
    rep = inspect_repo(GitTree(pc, sha))
    missing_after = sh("git", "-C", str(pc), "rev-list", "--objects", "--missing=print", "HEAD").count("?")
    assert missing_before >= 5
    assert rep["files"]["bytes"] is None
    assert missing_after == missing_before
    assert any("size" in n for n in rep["not_observed"]), rep["not_observed"]
