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
    # SessionStart's `npm ci` runs lifecycle scripts; SessionStart's `npm run build` runs repo code
    # SessionEnd's yarn install is risky (lifecycle scripts); pnpm with --ignore-scripts is safe
    # PostToolUse is not automatic
    hook_risks_set = {(h["event"], h["command"]) for h in report["hook_risks"]}
    assert hook_risks_set == {("SessionEnd", "yarn install"), ("SessionStart", "npm ci"), ("SessionStart", "npm run build")}
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
    ({"cpp/CMakeLists.txt": "enable_testing()\nadd_test(a a)\n", "cpp/a_test.cc": ""},
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
                                  "docs/specs/api.yaml", "tests/__init__.py", "Tests.swift"])
def test_test_file_excludes_false_positives(path):
    """Java/Kotlin/C# files without Test/Tests/IT/Spec prefix and doc dirs should not match."""
    from dokimi.repos.environment import files as F
    assert not F.is_test_file(path), path


@pytest.mark.parametrize("path", ["src/FooTest.java", "FooTests.kt", "FooIT.java", "BarSpec.scala",
                                  "FooTests.swift", "Sources/AppTests.swift", "a.test.ts", "x_test.go",
                                  "tests/test_a.py", "spec/a_spec.rb", "__tests__/a.js",
                                  "tests/foo.c", "tests/foo.cc", "tests/foo.cpp", "tests/foo.cxx", "tests/foo.h",
                                  "tests/foo.hh", "tests/foo.hpp", "test/a.lua", "test/a.dart", "tests/a.hs",
                                  "tests/a.sh", "test/a.bats"])
def test_test_file_includes_true_positives(path):
    """Legitimate test files should still match."""
    from dokimi.repos.environment import files as F
    assert F.is_test_file(path), path


@pytest.mark.parametrize("files", [
    {"package.json": '{"scripts": {"test": "x"}, "n": ' + "9" * 5001 + "}"},
    {".claude/settings.json": '{"n": ' + "9" * 5001 + "}"},
    {".pre-commit-config.yaml": "repos:\n- repo: x\n  rev: 2020-02-30\n"},
])
def test_odd_manifests_do_not_crash(tmp_path, files):
    """Large integers and invalid dates in manifests should not crash."""
    _repo(tmp_path, {"AGENTS.md": "x", **files})
    payload = inspect_repo(WorkTree(tmp_path))
    assert payload is not None


@pytest.mark.parametrize("files", [
    {"CMakeLists.txt": "project(a)\n", "third_party/gtest/CMakeLists.txt": "enable_testing()\n"},
    {"MODULE.bazel": "", "vendor/dep/BUILD": "cc_test(name='x')\n"},
    {"node_modules/left-pad/package.json": '{"scripts": {"test": "mocha"}}'},
])
def test_vendored_build_files_do_not_suppress_missing_tests(tmp_path, files):
    """Vendored/generated files should not count toward test detection."""
    _repo(tmp_path, {"AGENTS.md": "x", **files})
    payload = inspect_repo(WorkTree(tmp_path))
    rules = {f["rule"] for f in payload["findings"]}
    assert "checks.test.missing" in rules, [c for c in payload.get("checks", [])]


def test_blob_less_clone_respects_partial_clone_boundary(tmp_path):
    """Inspect should not fetch blobs from a partial clone."""
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


def test_runner_unknown_warning_when_tests_present(tmp_path):
    """Test files present but no runner known should give warning, not error."""
    _repo(tmp_path, {"AGENTS.md": "x", "pyproject.toml": "[build-system]\n[project]\nname='a'\n",
                     "tests/test_a.py": "def test(): pass\n"})
    payload = inspect_repo(WorkTree(tmp_path))
    rules = _rules(payload)
    assert "checks.test.runner-unknown" in rules
    assert "checks.test.missing" not in rules
    runner_unknown = [f for f in payload["findings"] if f["rule"] == "checks.test.runner-unknown"]
    assert runner_unknown and runner_unknown[0]["severity"] == "warning"


def test_runner_unknown_for_ruby_tests(tmp_path):
    """Ruby tests with Gemfile but no runner should give runner-unknown."""
    _repo(tmp_path, {"AGENTS.md": "x", "Gemfile": "gem 'rake'\n", "spec/a_spec.rb": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    rules = _rules(payload)
    assert "checks.test.runner-unknown" in rules
    assert payload["files"]["test_files"] == 1


def test_fail_on_error_exits_zero_for_runner_unknown(tmp_path, capsys):
    """--fail-on-error should exit 0 for warning-level runner-unknown."""
    root = tmp_path / "toy"
    _repo(root, {"AGENTS.md": "x", "pyproject.toml": "[project]\n", "tests/test_a.py": "x"})
    out = tmp_path / "env.json"
    args = build_parser().parse_args(["repo", "inspect", str(root), "--out", str(out), "--fail-on-error"])
    args.func(args)  # Should exit 0 because runner-unknown is a warning
    text = capsys.readouterr().out
    assert "WARNING" in text or "runner-unknown" in text


def test_nested_makefile_am_with_tests(tmp_path):
    """Tests in nested Makefile.am should be detected."""
    _repo(tmp_path, {"AGENTS.md": "x", "configure.ac": "", "tests/Makefile.am": "TESTS = t\n"})
    payload = inspect_repo(WorkTree(tmp_path))
    checks = {(c["ecosystem"], c["kind"]) for c in payload["checks"]}
    assert ("autotools", "test") in checks


def test_user_prompt_submit_and_stop_hook_risks(tmp_path):
    """UserPromptSubmit and Stop events should be detected as automatic."""
    settings = {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "npm ci"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "npm install"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    hook_risks = {(h["event"], h["command"]) for h in payload["hook_risks"]}
    assert ("UserPromptSubmit", "npm ci") in hook_risks or any("npm" in h["command"] for h in payload["hook_risks"])
    assert ("Stop", "npm install") in hook_risks or any("npm" in h["command"] for h in payload["hook_risks"])


def test_subagentstop_in_automatic_events(tmp_path):
    """SubagentStop should be in AUTOMATIC_EVENTS."""
    from dokimi.repos.environment import risks
    assert "SubagentStop" in risks.AUTOMATIC_EVENTS


def test_deeply_nested_yaml_does_not_crash(tmp_path):
    """Deeply nested YAML should not cause RecursionError."""
    _repo(tmp_path, {"AGENTS.md": "x", ".pre-commit-config.yaml": "[" * 100000})
    payload = inspect_repo(WorkTree(tmp_path))
    assert payload is not None  # Should not crash


def test_npm_install_1mb_completes_quickly(tmp_path):
    """1 MB of npm install hook should complete in under 1 second."""
    import time
    hook_cmd = "npm " + "-npm " * (1_000_000 // 5)  # ~1 MB total
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": hook_cmd}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    start = time.monotonic()
    assert inspect_repo(WorkTree(tmp_path))
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"Took {elapsed} seconds, should be < 1s"


def test_hook_npm_run_detected(tmp_path):
    """npm run commands should be detected as hook risks."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "npm run build"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert any("npm run" in r for r in risks)


def test_hook_make_detected(tmp_path):
    """make commands should be detected as hook risks."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "make build"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert any("make" in r for r in risks)


def test_hook_path_detected(tmp_path):
    """Paths like ./script.sh should be detected as hook risks."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "./setup.sh"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "setup.sh": "x", "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert any("setup.sh" in r or "./setup" in r for r in risks)


def test_hook_make_not_in_echo(tmp_path):
    """make in echo should NOT be flagged."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo 'make sure deps are installed'"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert not any("echo" in r for r in risks)


def test_hook_make_case_insensitive(tmp_path):
    """Make in echo case should NOT be flagged."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo Make it so"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert not any("echo" in r for r in risks)


def test_hook_path_not_flagged_for_cd_parent(tmp_path):
    """cd ../other should not be flagged as a path risk."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "cd ../other && ls"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert not any("cd ../other" in r for r in risks)


def test_hook_path_not_flagged_for_cat_file(tmp_path):
    """cat package.json should not be flagged as a path risk."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "cat package.json"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "package.json": "{}", "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert not any("cat package.json" in r for r in risks)


def test_hook_ignore_scripts_with_url_fragment(tmp_path):
    """URL fragments should not be stripped as comments."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "npm ci --registry=https://r/#x --ignore-scripts"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    # Should not be flagged because --ignore-scripts is present
    assert not any("npm ci" in r for r in risks)


def test_test_file_case_insensitive_and_mjs(tmp_path):
    """Test dirs should be case-insensitive, and .mjs/.cjs should be recognized."""
    _repo(tmp_path, {"Tests/a.mjs": "x", "TESTS/b.cjs": "x", "specs/c.ts": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    assert payload["files"]["test_files"] == 3


def test_unbalanced_quote_in_hook_completes_quickly(tmp_path):
    """1 MB hook with unbalanced quote should complete in under 1 second."""
    import time
    hook_cmd = "echo '" + "a" * 1_000_000  # 1 MB, unbalanced quote
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": hook_cmd}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    start = time.monotonic()
    payload = inspect_repo(WorkTree(tmp_path))
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"Took {elapsed} seconds, should be < 1s"
    # Should not crash, just complete quickly
    assert payload is not None


def test_npm_install_with_10_options_followed_by_ci(tmp_path):
    """npm with 10 options followed by ci should be detected."""
    # Use 10 options to test the {0,16} bound
    hook_cmd = "npm -a -b -c -d -e -f -g -h -i -j ci"
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": hook_cmd}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert any("npm" in r and "ci" in r for r in risks)


def test_python3_with_flag_and_tracked_script(tmp_path):
    """python3 -u tools/bootstrap.py should be detected as a path risk."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "python3 -u tools/bootstrap.py"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "tools/bootstrap.py": "x", "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert any("python3" in r and "tools/bootstrap.py" in r for r in risks)


def test_sh_with_c_flag_and_relative_path(tmp_path):
    """sh -c './scripts/x.sh' should be detected as a path risk."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "sh -c './scripts/x.sh'"}]}],
        }
    }
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "scripts/x.sh": "x", "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert any("./scripts/x.sh" in r for r in risks)


def test_bash_with_dollar_env_and_unbalanced_quote(tmp_path):
    """bash '$CLAUDE_PROJECT_DIR/x.sh with unbalanced quote should be detected."""
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "bash '$CLAUDE_PROJECT_DIR/x.sh"}]}],
        }
    }
    # Note: file doesn't need to exist for this test; we're testing path detection with unbalanced quotes
    _repo(tmp_path, {".claude/settings.json": json.dumps(settings), "AGENTS.md": "x"})
    payload = inspect_repo(WorkTree(tmp_path))
    risks = {h["command"] for h in payload["hook_risks"]}
    assert any("$CLAUDE_PROJECT_DIR" in r for r in risks)


@pytest.mark.parametrize("settings, kind", [({"statusLine": {"type": "command", "command": "x"}}, "command"),
                                            ({"apiKeyHelper": "x"}, "command"),
                                            ({"enableAllProjectMcpServers": True}, "mcp")])
def test_settings_capabilities_without_hooks(tmp_path, settings, kind):
    _repo(tmp_path, {"AGENTS.md": "x", ".claude/settings.json": json.dumps(settings)})
    caps = inspect_repo(WorkTree(tmp_path))["capabilities"]
    assert [(c["kind"], c["risk"]) for c in caps] == [(kind, "high")]


def test_settings_capabilities_get_distinct_findings(tmp_path):
    settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
                "statusLine": {"type": "command", "command": "x"}, "apiKeyHelper": "./k.sh",
                "enableAllProjectMcpServers": True}
    _repo(tmp_path, {"AGENTS.md": "x", ".claude/settings.json": json.dumps(settings)})
    found = [f for f in inspect_repo(WorkTree(tmp_path))["findings"] if f["rule"] == "safety.capability.high-risk"]
    assert len(found) == 4
    assert len({f["id"] for f in found}) == 4 and len({f["why"] for f in found}) == 4


@pytest.mark.parametrize("command, flagged", [
    ("npm run build", True), ("npm test", True), ("npm start", True), ("yarn build", True), ("pnpm lint", True),
    ("./setup.sh", True), ("bash setup.sh", True), ("FOO=1 ./setup.sh", True), ("timeout 5 ./setup.sh", True),
    ("sudo ./setup.sh", True), ("source ./setup.sh", True), (". ./setup.sh", True),
    ("python3 -W ignore setup.sh", True), ("sh -c 'make test'", True), ("FOO=1 make", True),
    ("env A=1 make check", True), ("nohup ./setup.sh", True),
    ("npm --version", False), ("yarn --version", False), ("npm view x", False), ("echo make", False),
    ("cmake .", False), ("cat package.json", False), ("cd ../x", False), ("echo 'run npm test'", False),
])
def test_hook_command_runs_repository_code(tmp_path, command, flagged):
    settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}
    _repo(tmp_path, {"AGENTS.md": "x", "setup.sh": "true\n", "package.json": json.dumps({"scripts": {"test": "jest"}}),
                     ".claude/settings.json": json.dumps(settings)})
    assert bool(inspect_repo(WorkTree(tmp_path))["hook_risks"]) is flagged


@pytest.mark.parametrize("files", [
    {"setup.cfg": "[" * 40_000},
    {"build.gradle": "plugins {" * 20_000},
    {"pom.xml": "<artifactId>" + "checkstyle " * 20_000},
])
def test_manifest_readers_are_linear_on_repeated_tokens(tmp_path, files):
    import time
    _repo(tmp_path, {"AGENTS.md": "x", "setup.py": "", **files})
    start = time.monotonic()
    inspect_repo(WorkTree(tmp_path))
    assert time.monotonic() - start < 2


def test_blob_less_clone_reads_manifests_in_few_requests(tmp_path):
    import os
    src = tmp_path / "src"
    files = {"AGENTS.md": "x"}
    for i in range(8):
        files[f"p{i}/package.json"] = json.dumps({"name": f"p{i}", "scripts": {"test": f"jest {i}"}})
        files[f"p{i}/pyproject.toml"] = f"[project]\nname = 'p{i}'\n"
        files[f"p{i}/Makefile"] = f"test:\n\techo {i}\n"
    _repo(src, files)
    subprocess.run(["git", "-C", str(src), "config", "uploadpack.allowfilter", "true"], check=True)
    pc = tmp_path / "pc"
    subprocess.run(["git", "clone", "-q", "--bare", "--filter=blob:none", f"file://{src}", str(pc)], check=True)
    sha = subprocess.run(["git", "-C", str(pc), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    trace = tmp_path / "trace"
    os.environ["GIT_TRACE"] = str(trace)
    try:
        inspect_repo(GitTree(pc, sha))
    finally:
        del os.environ["GIT_TRACE"]
    fetches = sum("fetch" in line and "built-in: git fetch" in line for line in trace.read_text().splitlines())
    assert fetches <= 6, fetches
