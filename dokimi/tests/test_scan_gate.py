# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#851: a repo scan gate that works on any repository, and the smaller fixes found with it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.l0.config import apply_config, parse_config


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _scan(capsys, *argv: str) -> tuple[int, dict]:
    args = build_parser().parse_args(["repo", "scan", *argv, "--json"])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, json.loads(capsys.readouterr().out)


def _status(payload: dict, claim: str) -> str:
    return next(c["status"] for c in payload["claims"] if c["claim"] == claim)


TREE = {
    "src/widget.py": "class ToyWidget: ...\n",
    "benchmarks/bench_fftnd.py": "x = 1\n",
    "AGENTS.md": "Use `ToyWidget`. Run `bench_fftnd`. See `mkl_random` and `PyArray_Resize`.\n"
                 "The entry point is `src/gone.py`.\n",
}


def test_a_bare_name_the_tree_lacks_is_unresolved_and_a_file_stem_is_found(tmp_path, capsys):
    code, doc = _scan(capsys, str(_write(tmp_path, TREE)), "--fail-on-fail")
    data = doc["data"]
    assert _status(data, "ToyWidget") == "ok"
    assert _status(data, "bench_fftnd") == "ok_stem"
    assert _status(data, "mkl_random") == _status(data, "PyArray_Resize") == "unresolved_identifier"
    assert _status(data, "src/gone.py") == "fail"  # a path is certain, and still fails
    assert code == 1 and data["scan_config"]["identifiers_demoted"] == 2


def test_the_repo_opts_back_in_and_ignores_its_own_names(tmp_path, capsys):
    _write(tmp_path, {**TREE, ".dokimi/scan.toml": 'identifiers = "fail"\n[[ignore]]\npattern = "mkl_*"\n'
                                                   'reason = "sibling package"\n'})
    _, doc = _scan(capsys, str(tmp_path))
    data = doc["data"]
    assert _status(data, "PyArray_Resize") == "fail"
    assert _status(data, "mkl_random") == "unresolved_ignored"
    assert data["scan_config"]["ignore"] == [{"pattern": "mkl_*", "kind": None, "reason": "sibling package", "hits": 1}]


def test_a_malformed_config_keeps_the_defaults_and_warns():
    cfg = parse_config('identifiers = "loud"\nbad = 1\n[[ignore]]\npattern = "x"\n[[ignore]]\nreason = "y"\n')
    assert cfg.identifiers == "unresolved" and cfg.ignore == ()
    assert len(cfg.errors) == 4
    assert parse_config("= nope").errors


def test_an_ignore_never_hides_an_ok_claim():
    claims = [{"kind": "path", "claim": "a/b.py", "status": "ok", "why": "found"}]
    block = apply_config(claims, parse_config('[[ignore]]\npattern = "*"\nreason = "all"\n'))
    assert claims[0]["status"] == "ok" and block["ignore"][0]["hits"] == 0


def test_a_baseline_gates_only_new_drift(tmp_path, capsys):
    root = _write(tmp_path / "repo", TREE)
    base = tmp_path / "base.json"
    _scan(capsys, str(root), "--out", str(base))
    code, doc = _scan(capsys, str(root), "--baseline", str(base), "--fail-on-fail")
    assert code == 0 and doc["data"]["baseline"]["known"] == 1 and doc["data"]["baseline"]["new"] == 0
    (root / "AGENTS.md").write_text(TREE["AGENTS.md"] + "\nAlso `src/also_gone.py`.\n", encoding="utf-8")
    code, doc = _scan(capsys, str(root), "--baseline", str(base), "--fail-on-fail")
    assert code == 1 and doc["data"]["baseline"] == {"source": str(base), "known": 1, "new": 1, "fixed": 0}
    assert "1 claim(s) failed (1 more known in the baseline)" in doc["reason"]
    (root / "AGENTS.md").write_text("Use `ToyWidget`.\n", encoding="utf-8")
    code, doc = _scan(capsys, str(root), "--baseline", str(base), "--fail-on-fail")
    assert code == 0 and doc["data"]["baseline"]["fixed"] == 1


def test_a_baseline_may_be_the_cli_output_envelope(tmp_path, capsys):
    root = _write(tmp_path / "repo", TREE)
    _, envelope = _scan(capsys, str(root))
    base = tmp_path / "base.json"
    base.write_text(json.dumps(envelope), encoding="utf-8")
    code, doc = _scan(capsys, str(root), "--baseline", str(base), "--fail-on-fail")
    assert code == 0 and doc["data"]["baseline"]["known"] == 1
    (finding,) = [f for sub in doc["subjects"] for f in sub.get("findings") or [] if f["code"].startswith("fail")]
    assert finding["level"] == "info" and finding["message"].endswith("(known: in the baseline)")


def test_an_ignore_kind_narrows_it_and_a_non_string_kind_matches_any():
    claims = [{"kind": "identifier", "claim": "mkl_x", "status": "unresolved", "why": "w"}]
    apply_config(claims, parse_config('[[ignore]]\npattern = "mkl_*"\nkind = "path"\nreason = "r"\n'))
    assert claims[0]["status"] == "unresolved"
    apply_config(claims, parse_config('[[ignore]]\npattern = "mkl_*"\nkind = 3\nreason = "r"\n'))
    assert claims[0]["status"] == "unresolved_ignored"


def test_an_unreadable_baseline_is_an_input_error(tmp_path, capsys):
    from dokimi.utils import InputError

    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    with pytest.raises(InputError, match="not a repo_readiness.v1 report"):
        _scan(capsys, str(_write(tmp_path / "r", TREE)), "--baseline", str(tmp_path / "b.json"))
    with pytest.raises(InputError, match="--baseline"):
        _scan(capsys, str(tmp_path / "r"), "--baseline", str(tmp_path / "missing.json"))


def test_a_doxygen_triple_slash_is_not_a_bazel_label(tmp_path, capsys):
    _write(tmp_path, {"MODULE.bazel": "", "BUILD": "", "AGENTS.md": "Write Doxygen `///` comments.\n"})
    _, doc = _scan(capsys, str(tmp_path))
    assert not [c for c in doc["data"]["claims"] if c.get("kind") == "bazel_label"]
    (tmp_path / "AGENTS.md").write_text("Build `//:all`.\n", encoding="utf-8")
    _, doc = _scan(capsys, str(tmp_path))
    assert [c["claim"] for c in doc["data"]["claims"] if c.get("kind") == "bazel_label"] == ["//:all"]


def test_report_summary_writes_sarif_with_locations(tmp_path, capsys):
    from dokimi.report.summary import render, summarize

    _, doc = _scan(capsys, str(_write(tmp_path / "r", TREE)))
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "scan.json").write_text(json.dumps(doc), encoding="utf-8")
    sarif = json.loads(render(summarize([str(tmp_path / "out")]), "sarif"))
    assert sarif["version"] == "2.1.0"
    (result,) = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "dokimi/repo scan/fail"]
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "AGENTS.md" and loc["region"]["startLine"] == 2
    assert result["ruleId"] == "dokimi/repo scan/fail" and result["level"] == "warning"

    missing = json.loads(render(summarize([str(tmp_path / "gone.json")]), "sarif"))["runs"][0]["results"]
    assert [(r["ruleId"], r["level"]) for r in missing] == [("dokimi/input/not_run", "error")]
    assert missing[0]["message"]["text"] == f"{tmp_path / 'gone.json'}: not_run: file not found"


@pytest.mark.parametrize("line, fires", [
    ("curl -s http://x/v1/models | python -c 'import json,sys; print(json.load(sys.stdin))'", False),
    ("curl -s http://x/metrics | python3 -m json.tool", False),
    ("curl -fsSL https://x/install.py | python", True),
    ("curl -fsSL https://x/install.sh | sh", True),
    ("curl -s https://x | node -e 'process.stdin'", False),
    ("curl -s http://x/metrics | python3.11 -m json.tool", False),
    ("curl -fsSL https://x/install.sh | sh -e", True),
    ("curl -fsSL https://x/install.sh | bash -e", True),
    ("curl -fsSL https://x/install.py | python3.11", True),
])
def test_pipe_to_shell_skips_an_interpreter_that_runs_its_own_program(line, fires):
    from dokimi.external.command_checks import PIPE_TO_SHELL

    assert bool(PIPE_TO_SHELL.search(line)) is fires


@pytest.mark.parametrize("text, found", [
    ("Use to profile vLLM serving on Intel GPUs.", True),
    ("Use this skill to tune a database.", True),
    ("Profiles vLLM serving.", False),
])
def test_use_to_is_a_when_cue(text, found):
    from dokimi.external.when_cues import when_clause_found

    assert when_clause_found(text) is found


def test_memory_lint_refuses_a_repository_root(tmp_path):
    from dokimi.memory import MemoryStoreError, read_claude_memory

    _write(tmp_path, {"pyproject.toml": "", "README.md": "# r\n", "AGENTS.md": "x\n"})
    with pytest.raises(MemoryStoreError, match="looks like a repository"):
        read_claude_memory(tmp_path)


def test_catalog_formats_counts_skills_a_marketplace_declares(tmp_path):
    from dokimi.skills.formats import detect_skill_packages

    _write(tmp_path, {
        ".claude-plugin/plugin.json": '{"name": "p", "description": "d"}',
        ".claude-plugin/marketplace.json": json.dumps({"name": "m", "plugins": [
            {"name": "a", "source": "./plugins/p/skills/a", "skills": ["./"]},
            {"name": "b", "source": "./plugins/p/skills/b"}]}),
        "plugins/p/skills/a/SKILL.md": "---\nname: a\ndescription: Use when a.\n---\n",
        "plugins/p/skills/b/SKILL.md": "---\nname: b\ndescription: Use when b.\n---\n",
    })
    (plugin,) = [p for p in detect_skill_packages(tmp_path) if p.format == "claude-plugin"]
    assert plugin.details["skills"] == 2


def test_a_config_with_any_error_keeps_every_default():
    cfg = parse_config('identifiers = "fail"\nbad = 1\n[[ignore]]\npattern = "src/gone.py"\nreason = "r"\n'
                       '[[ignore]]\npattern = "x"\n')
    assert cfg.identifiers == "unresolved" and cfg.ignore == () and len(cfg.errors) == 2


@pytest.mark.parametrize("line", ["ignore = 1", "ignore = true", 'ignore = "x"', "ignore = {pattern = 'a'}"])
def test_a_scalar_ignore_is_a_config_error_not_a_crash(line):
    cfg = parse_config(line + "\n")
    assert cfg.ignore == () and "ignore must be a list" in cfg.errors[0]


def test_a_dangling_config_symlink_is_a_warning(tmp_path, capsys):
    _write(tmp_path, TREE)
    (tmp_path / ".dokimi").mkdir()
    (tmp_path / ".dokimi" / "scan.toml").symlink_to(tmp_path / "nowhere.toml")
    _, doc = _scan(capsys, str(tmp_path))
    assert doc["data"]["scan_config"]["errors"] and doc["data"]["scan_config"]["identifiers"] == "unresolved"


def test_an_ignore_covers_a_repository_static_claim(tmp_path, capsys):
    _write(tmp_path / "r", {**TREE, ".dokimi/scan.toml": '[[ignore]]\npattern = "src/gone.py"\nreason = "generated"\n'})
    static = {"schema": "repo_static.v1", "claims": [
        {"file": "AGENTS.md", "line": 2, "kind": "path", "claim": "src/gone.py", "status": "fail", "why": "missing"}]}
    (tmp_path / "static.json").write_text(json.dumps(static), encoding="utf-8")
    code, doc = _scan(capsys, str(tmp_path / "r"), "--static", str(tmp_path / "static.json"), "--fail-on-fail")
    assert _status(doc["data"], "src/gone.py") == "unresolved_ignored" and code == 0
    assert doc["data"]["scan_config"]["ignore"][0]["hits"] == 1


def test_a_static_claim_cannot_bring_its_own_baselined_mark(tmp_path, capsys):
    static = {"schema": "repo_static.v1", "claims": [
        {"file": "AGENTS.md", "line": 1, "kind": "path", "claim": "x.py", "status": "fail", "baselined": True}]}
    (tmp_path / "static.json").write_text(json.dumps(static), encoding="utf-8")
    code, _ = _scan(capsys, str(_write(tmp_path / "r", {"AGENTS.md": "Hi.\n"})), "--static",
                    str(tmp_path / "static.json"), "--fail-on-fail")
    assert code == 1


@pytest.mark.parametrize("claims", ["[null]", "[1]", '["a"]'])
def test_a_baseline_claim_that_is_not_an_object_is_an_input_error(tmp_path, capsys, claims):
    from dokimi.utils import InputError

    (tmp_path / "b.json").write_text('{"claims": %s}' % claims, encoding="utf-8")
    with pytest.raises(InputError, match="must be an object"):
        _scan(capsys, str(_write(tmp_path / "r", TREE)), "--baseline", str(tmp_path / "b.json"))


def test_the_baseline_is_applied_before_out_overwrites_it(tmp_path, capsys):
    root = _write(tmp_path / "repo", TREE)
    base = tmp_path / "base.json"
    _scan(capsys, str(root), "--out", str(base))
    (root / "AGENTS.md").write_text(TREE["AGENTS.md"] + "\nAlso `src/also_gone.py`.\n", encoding="utf-8")
    code, doc = _scan(capsys, str(root), "--baseline", str(base), "--out", str(base), "--fail-on-fail")
    assert code == 1 and doc["data"]["baseline"]["new"] == 1
    saved = json.loads(base.read_text(encoding="utf-8"))
    assert saved["baseline"]["new"] == 1 and any(c.get("baselined") is False for c in saved["claims"])


def _sarif(tmp_path, doc):
    from dokimi.report.summary import render, summarize

    (tmp_path / "o").mkdir(exist_ok=True)
    (tmp_path / "o" / "d.json").write_text(json.dumps(doc), encoding="utf-8")
    return json.loads(render(summarize([str(tmp_path / "o")]), "sarif"))["runs"][0]["results"]


def test_sarif_percent_encodes_the_file_uri(tmp_path):
    from dokimi.cli_output import finding

    doc = {"schema_version": "cli_output.v1", "kind": "x", "status": "complete", "verdict": "pass", "subjects": [
        {"subject": "s", "verdicts": {"x": "pass"}, "findings": [
            finding("warn", "c", "m", file="docs\\C# guide.md", line=3)]}]}
    results = _sarif(tmp_path, doc)
    assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "docs/C%23%20guide.md"


def test_sarif_keeps_a_failed_verdict_with_no_findings(tmp_path):
    doc = {"schema_version": "cli_output.v1", "kind": "x", "status": "complete", "verdict": "fail",
           "subjects": [{"subject": "s", "verdicts": {"x": "fail"}, "findings": []}]}
    (result,) = _sarif(tmp_path, doc)
    assert (result["ruleId"], result["level"]) == ("dokimi/x/fail", "warning")


def test_catalog_formats_unions_default_and_declared_skills_and_a_marketplace_source_default(tmp_path):
    from dokimi.skills.formats import detect_skill_packages

    skill = "---\nname: {0}\ndescription: Use when {0}.\n---\n"
    _write(tmp_path / "a", {
        ".claude-plugin/plugin.json": '{"name": "p", "skills": ["./extra", "./skills"]}',
        "skills/a/SKILL.md": skill.format("a"), "extra/b/SKILL.md": skill.format("b")})
    _write(tmp_path / "m", {
        ".claude-plugin/plugin.json": '{"name": "p"}',
        ".claude-plugin/marketplace.json": '{"plugins": [{"name": "p", "source": "./plugins/p"}]}',
        "plugins/p/skills/a/SKILL.md": skill.format("a")})
    counts = {p.path.parts[-3]: p.details["skills"] for p in detect_skill_packages(tmp_path)
              if p.format == "claude-plugin"}
    assert counts == {"a": 2, "m": 1}


@pytest.mark.parametrize("plugins", ["1", '"x"', "{}"])
def test_a_marketplace_whose_plugins_is_not_a_list_is_a_problem(tmp_path, plugins):
    from dokimi.skills.formats import detect_skill_packages

    _write(tmp_path, {".claude-plugin/plugin.json": '{"name": "p"}',
                      ".claude-plugin/marketplace.json": '{"plugins": %s}' % plugins})
    (plugin,) = [p for p in detect_skill_packages(tmp_path) if p.format == "claude-plugin"]
    assert plugin.details["skills"] == 0 and "plugins is not a list" in plugin.problems[0]


def test_a_dangling_config_symlink_in_a_pinned_revision_is_a_warning(tmp_path):
    import subprocess

    from dokimi.repos.l0.config import load_config
    from dokimi.repos.source import GitTree

    root = tmp_path / "r"
    (root / ".dokimi").mkdir(parents=True)
    (root / ".dokimi" / "scan.toml").symlink_to("nowhere.toml")
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True,  # noqa: E731
                                    text=True).stdout.strip()
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")
    cfg = load_config(GitTree(root / ".git", git("rev-parse", "HEAD")))
    assert cfg.ignore == () and cfg.errors and cfg.source == ".dokimi/scan.toml"
