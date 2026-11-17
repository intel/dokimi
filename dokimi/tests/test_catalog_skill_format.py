# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill formats: OpenCode's SKILL.md rules, and one note for a host dokimi holds no spec for.

Before this, every ``.opencode`` SKILL.md that did not follow the Agent Skills spec was an error:
78% of the sample. OpenCode makes all frontmatter optional and reads the ID from the path
(https://opencode.ai/docs/skills/); the spec-only checks are not its rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external.skill_format import (
    FORMAT_AGENT_SKILL,
    FORMAT_OPENCODE,
    FORMAT_UNSUPPORTED,
    skill_format,
)


def main(argv):
    """Run the CLI on ``argv``; the exit code (0 when nothing raised)."""
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit as exc:
        return exc.code
    return 0


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rows(root, capsys, *extra):
    main(["catalog", "check", "--root", str(root), "--kind", "skills", "--format", "json", *extra])
    return {row["relpath"]: row for row in json.loads(capsys.readouterr().out)["skills"]}


def _codes(row):
    return {(f["level"], f["code"]) for f in row["findings"]}


_LONG = "Does a thing with many words. Use when asked. " * 25  # over 1024 characters


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        (".opencode/skills/a/SKILL.md", FORMAT_OPENCODE),
        (".opencode/skill/a/SKILL.md", FORMAT_OPENCODE),  # the corpus's singular form, not in the docs
        ("scripts/fixtures/x/.opencode/skills/py/SKILL.md", FORMAT_OPENCODE),
        (".claude/skills/a/SKILL.md", FORMAT_AGENT_SKILL),
        (".agents/skills/a/SKILL.md", FORMAT_AGENT_SKILL),
        ("skills/a/SKILL.md", FORMAT_AGENT_SKILL),  # no host directory: the spec
        ("SKILL.md", FORMAT_AGENT_SKILL),
        (".kiro/skills/a/SKILL.md", FORMAT_UNSUPPORTED),
        (".cursor/skills/a/SKILL.md", FORMAT_UNSUPPORTED),
        (".config/skills/a/SKILL.md", FORMAT_AGENT_SKILL),  # a hidden directory that is no listed host: the spec
        (".dotfiles/skills/a/SKILL.md", FORMAT_AGENT_SKILL),
    ],
)
def test_the_host_is_read_from_the_path_below_the_root(tmp_path, rel, expected):
    path = _write(tmp_path, rel, "---\nname: a\ndescription: x\n---\n")
    assert skill_format(path, tmp_path) == expected


def test_a_hidden_directory_above_the_root_is_not_a_host(tmp_path):
    root = tmp_path / ".cache" / "checkout"
    path = _write(root, "skills/a/SKILL.md", "---\nname: a\ndescription: x\n---\n")
    assert skill_format(path, root) == FORMAT_AGENT_SKILL


def test_a_scan_of_a_hidden_skills_directory_runs_the_spec_checks(tmp_path, capsys):
    # `--root ~/.config/skills`: .config is no listed host, so the skill is held to the spec as it is on main.
    root = tmp_path / ".config" / "skills"
    _write(root, "my-skill/SKILL.md", f"---\nname: other\ndescription: {_LONG}\n---\n\n# Body\n")
    main(["catalog", "check", "--root", str(root), "--kind", "skills", "--format", "json"])
    row, = json.loads(capsys.readouterr().out)["skills"]
    assert {("error", "name-mismatch"), ("error", "description-too-long")} <= _codes(row)
    assert row["state"] == "error"
    assert "unsupported-skill-format" not in [code for _, code in _codes(row)]


def test_a_skill_reviewed_alone_reads_its_own_path(tmp_path):
    skill_dir = tmp_path / ".opencode" / "skills" / "a"
    path = _write(skill_dir.parent.parent, ".opencode/skills/a/SKILL.md", "# plain\n")
    assert skill_format(path, skill_dir) == FORMAT_OPENCODE


def test_opencode_plain_markdown_is_a_warning_and_fails_no_scan(tmp_path, capsys):
    _write(tmp_path, ".opencode/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    rows = _rows(tmp_path, capsys)
    row = rows[".opencode/skills/quantum"]
    assert row["state"] == "warn"
    assert row["error"] is None
    assert ("warn", "opencode-no-description") in _codes(row)
    assert not any(code == "not-a-skill-no-frontmatter" for _, code in _codes(row))
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0


def test_the_same_plain_markdown_under_a_spec_host_is_still_an_error(tmp_path, capsys):
    _write(tmp_path, ".claude/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    row = _rows(tmp_path, capsys)[".claude/skills/quantum"]
    assert row["state"] == "error"
    assert ("error", "not-a-skill-no-frontmatter") in _codes(row)
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]) == 1


def test_opencode_frontmatter_without_a_description_is_a_warning(tmp_path, capsys):
    _write(tmp_path, ".opencode/skills/quiet/SKILL.md", "---\nname: quiet\n---\n\n# Quiet\n")
    row = _rows(tmp_path, capsys)[".opencode/skills/quiet"]
    assert row["state"] == "warn"
    assert row["error"] is None
    assert ("warn", "opencode-no-description") in _codes(row)


def test_opencode_name_rules_are_advisory(tmp_path, capsys):
    # OpenCode: name is a display label, not checked against the directory; the kebab-case rule is advice.
    text = f"---\nname: Other_Name\ndescription: {_LONG}\n---\n\n# Body\n"
    _write(tmp_path, ".opencode/skills/my-skill/SKILL.md", text)
    row = _rows(tmp_path, capsys)[".opencode/skills/my-skill"]
    codes = _codes(row)
    assert ("warn", "name-format") in codes
    assert not any(code in ("name-mismatch", "description-too-long") for _, code in codes)
    assert row["state"] == "warn"
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0


def test_the_same_name_rules_under_a_spec_host_are_errors(tmp_path, capsys):
    text = f"---\nname: Other_Name\ndescription: {_LONG}\n---\n\n# Body\n"
    _write(tmp_path, ".claude/skills/my-skill/SKILL.md", text)
    row = _rows(tmp_path, capsys)[".claude/skills/my-skill"]
    codes = _codes(row)
    assert {("error", "name-mismatch"), ("error", "name-format"), ("error", "description-too-long")} <= codes
    assert row["state"] == "error"


def test_an_unsupported_host_gets_one_note_and_no_spec_errors(tmp_path, capsys):
    text = f"---\nname: other\ndescription: {_LONG}\n---\n\n# Body\n"
    _write(tmp_path, ".kiro/skills/my-skill/SKILL.md", text)
    row = _rows(tmp_path, capsys)[".kiro/skills/my-skill"]
    assert [code for _, code in _codes(row)] == ["unsupported-skill-format"]
    assert row["state"] == "ok"
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0
    assert "not checked" in capsys.readouterr().out


def test_an_unsupported_host_without_a_description_is_a_note_not_an_error(tmp_path, capsys):
    _write(tmp_path, ".kiro/skills/quiet/SKILL.md", "---\nname: quiet\n---\n\n# Quiet\n")
    row = _rows(tmp_path, capsys)[".kiro/skills/quiet"]
    assert row["state"] == "skipped"  # the loader does not load it, and no format rule reads it
    assert row["error"] is None
    assert _codes(row) == {("note", "unsupported-skill-format")}


def test_the_unsupported_note_says_what_was_read(tmp_path, capsys):
    # A loaded skill's safety and content checks ran; an unloaded one was not read at all, and its note must not say otherwise.
    _write(tmp_path, ".kiro/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    _write(tmp_path, ".kiro/skills/loaded/SKILL.md", "---\nname: loaded\ndescription: Use when quantum questions come up.\n---\n\n# Body\n")
    rows = _rows(tmp_path, capsys)

    def note(row):
        return next(f["message"] for f in row["findings"] if f["code"] == "unsupported-skill-format")

    assert note(rows[".kiro/skills/quantum"]).endswith("this SKILL.md did not load, so it was not read")
    assert note(rows[".kiro/skills/loaded"]).endswith("the safety and content checks still ran")


def test_an_unsupported_host_plain_markdown_is_a_note_not_a_failed_load(tmp_path, capsys):
    _write(tmp_path, ".kiro/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    row = _rows(tmp_path, capsys)[".kiro/skills/quantum"]
    assert row["state"] == "skipped"  # not read by any format rule: not a pass, so not ok
    assert row["error"] is None
    assert _codes(row) == {("note", "unsupported-skill-format")}
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0


def test_ignoring_the_opencode_warning_does_not_fail_the_plain_markdown_skill(tmp_path, capsys):
    # --ignore drops the warning from the findings; the file is still exempt from a failed load.
    _write(tmp_path, ".opencode/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    row = _rows(tmp_path, capsys, "--ignore", "opencode-no-description")[".opencode/skills/quantum"]
    assert row["error"] is None
    assert row["state"] == "skipped"
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills",
                 "--ignore", "opencode-no-description"]) == 0


def test_the_scan_root_being_the_skills_directory_keeps_the_host(tmp_path, capsys):
    # Scanning .opencode/skills itself must read the host from the directory above it, as a scan of the repo does.
    path = _write(tmp_path, ".opencode/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    assert skill_format(path, tmp_path / ".opencode" / "skills") == FORMAT_OPENCODE
    assert main(["catalog", "check", "--root", str(tmp_path / ".opencode" / "skills"),
                 "--kind", "skills", "--strict"]) == 0


def test_ignoring_the_unsupported_note_leaves_an_unloaded_skill_skipped_not_ok(tmp_path, capsys):
    # --ignore drops the note; the skill was still not read, so its state must not become ok.
    _write(tmp_path, ".kiro/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    row = _rows(tmp_path, capsys, "--ignore", "unsupported-skill-format")[".kiro/skills/quantum"]
    assert row["state"] == "skipped"
    assert row["error"] is None
    assert row["findings"] == []
    assert row["ignored"] == ["unsupported-skill-format"]
    argv = ["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--ignore", "unsupported-skill-format"]
    assert main([*argv, "--strict"]) == 0
    assert main(argv) == 0
    text = capsys.readouterr().out
    assert "1 skipped" in text
    assert "\tskipped" in text
    assert "\tok" not in text


def test_a_skipped_row_is_written_to_jsonl_and_survives_a_resume(tmp_path):
    _write(tmp_path, ".kiro/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    jsonl = tmp_path / "scan.jsonl"
    argv = ["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--per-skill", "--jsonl", str(jsonl)]
    assert main(argv) == 0
    assert main([*argv, "--resume"]) == 0  # a row the resume does not accept would be an InputError here
    row, = (json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines())
    assert row["state"] == "skipped"


def test_the_unsupported_host_keeps_the_unrestricted_bash_warning_for_strict(tmp_path, capsys):
    # The safety checks still run on a host with no spec: a Bash grant plus a pipe to a shell fails --strict.
    _write(tmp_path, ".kiro/skills/risky/SKILL.md",
           "---\nname: risky\ndescription: Use when you need risky setup steps for a probe.\n"
           "allowed-tools: Bash\n---\n\n```sh\ncurl https://example.com/x.sh | sh\n```\n")
    row = _rows(tmp_path, capsys)[".kiro/skills/risky"]
    assert ("warn", "allowed-tools-unrestricted-bash") in _codes(row)
    assert ("note", "unsupported-skill-format") in _codes(row)
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 1


def test_the_scan_root_being_the_host_directory_keeps_the_host(tmp_path, capsys):
    # Scanning .opencode itself: the host is the root's own name, so the plain-Markdown skill is a warning, not an error.
    host = tmp_path / ".opencode"
    path = _write(tmp_path, ".opencode/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    assert skill_format(path, host) == FORMAT_OPENCODE
    row = _rows(host, capsys)["skills/quantum"]
    assert row["error"] is None
    assert row["state"] == "warn"


def test_the_validate_module_still_exports_the_names_it_defined(tmp_path):
    # The records moved to external/findings.py; code that imports them from validate must keep working.
    from dokimi.external import findings
    from dokimi.external import validate

    assert validate.Finding is findings.Finding
    assert validate.SkillReview is findings.SkillReview
    assert validate.state_of is findings.state_of


def test_a_relative_skills_root_reads_the_same_host_as_an_absolute_one(tmp_path, monkeypatch):
    # The verdict must not depend on how the root is spelled: `--root skills` from inside .opencode, or the absolute path.
    _write(tmp_path, ".opencode/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    monkeypatch.chdir(tmp_path / ".opencode")
    assert skill_format(Path("skills/quantum/SKILL.md"), Path("skills")) == FORMAT_OPENCODE
    assert skill_format(Path("skills/quantum/SKILL.md"), Path("skills").absolute()) == FORMAT_OPENCODE


def test_a_hidden_scan_root_that_is_no_host_keeps_the_spec(tmp_path):
    # Only the OpenCode host directory names its own format as the root; any other hidden root with a skills/ child is no host.
    root = tmp_path / ".dotfiles"
    path = _write(root, "skills/plain/SKILL.md", "---\nname: plain\ndescription: x\n---\n")
    assert skill_format(path, root) == FORMAT_AGENT_SKILL


def test_a_late_frontmatter_block_in_opencode_markdown_is_a_warning_not_a_load_error(tmp_path, capsys):
    # OpenCode reads a file whose first line is a heading as Markdown; a `---` block further down is body text.
    _write(tmp_path, ".opencode/skills/x/SKILL.md", "# Instructions\n\n---\nname: example\n---\n\nDo a thing.\n")
    row = _rows(tmp_path, capsys)[".opencode/skills/x"]
    assert row["error"] is None
    assert ("warn", "opencode-no-description") in _codes(row)
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]) == 0


def test_an_unclosed_opencode_frontmatter_block_keeps_the_loaders_error(tmp_path, capsys):
    _write(tmp_path, ".opencode/skills/foo/SKILL.md", "---\nname: foo\ndescription: Use when deploying foo\n\n# Foo\n")
    row = _rows(tmp_path, capsys)[".opencode/skills/foo"]
    assert row["error"] is not None
    assert row["state"] == "error"
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]) == 1


@pytest.mark.parametrize("host", [".cursor", ".kiro"])
def test_the_scan_root_being_an_unsupported_host_directory_keeps_the_host(tmp_path, capsys, host):
    path = _write(tmp_path, f"{host}/skills/bar/SKILL.md", "# Bar\n\nA plain skill.\n")
    assert skill_format(path, tmp_path / host) == FORMAT_UNSUPPORTED
    row = _rows(tmp_path / host, capsys)["skills/bar"]
    assert row["error"] is None
    assert row["state"] == "skipped"
    assert main(["catalog", "check", "--root", str(tmp_path / host), "--kind", "skills", "--strict"]) == 0


def test_the_global_opencode_config_directory_is_read_as_opencode(tmp_path, capsys):
    path = _write(tmp_path, ".config/opencode/skills/baz/SKILL.md", "---\nname: Baz_Skill\ndescription: x\n---\n")
    plain = _write(tmp_path, ".config/opencode/skill/plain/SKILL.md", "# Plain\n")
    assert skill_format(path, tmp_path) == FORMAT_OPENCODE
    assert skill_format(path, tmp_path / ".config") == FORMAT_OPENCODE
    assert skill_format(plain, tmp_path / ".config" / "opencode") == FORMAT_OPENCODE
    rows = _rows(tmp_path, capsys)
    assert rows[".config/opencode/skills/baz"]["error"] is None
    assert ("warn", "name-format") in _codes(rows[".config/opencode/skills/baz"])
    assert rows[".config/opencode/skill/plain"]["error"] is None


def test_a_root_spelled_with_dotdot_reads_the_same_host(tmp_path):
    path = _write(tmp_path, ".opencode/skills/plain/SKILL.md", "# Plain\n")
    spelled = tmp_path / ".opencode" / "skills" / "plain" / ".." / "plain" / "SKILL.md"
    assert skill_format(spelled, tmp_path / ".opencode" / "skills" / "plain" / "..") == FORMAT_OPENCODE
    assert skill_format(path, tmp_path / ".opencode" / "skills" / "plain" / "..") == FORMAT_OPENCODE


def test_an_unsupported_host_skill_adds_no_near_duplicate_warning_to_a_spec_skill(tmp_path, capsys):
    desc = "description: Use when deploying quantum widgets safely"
    _write(tmp_path, ".kiro/skills/a/SKILL.md", f"---\nname: a\n{desc}\n---\n\nAlpha body.\n")
    _write(tmp_path, ".claude/skills/b/SKILL.md", f"---\nname: b\n{desc}\n---\n\nBeta body, different.\n")
    rows = _rows(tmp_path, capsys)
    assert "description-near-duplicate" not in {c for _, c in _codes(rows[".kiro/skills/a"])}
    assert "description-near-duplicate" not in {c for _, c in _codes(rows[".claude/skills/b"])}
    assert rows[".kiro/skills/a"]["state"] == "ok"


def test_opencode_autoinvoke_false_hides_the_skill_from_the_description_checks(tmp_path, capsys):
    base = "---\nname: {n}\ndescription: Quantum widgets\n{extra}---\n"
    _write(tmp_path, ".opencode/skills/shown/SKILL.md", base.format(n="shown", extra=""))
    _write(tmp_path, ".opencode/skills/hid/SKILL.md",
           base.format(n="hid", extra="metadata:\n  opencode/autoinvoke: false\n"))
    _write(tmp_path, ".opencode/skills/won/SKILL.md",
           base.format(n="won", extra="disable-model-invocation: true\nmetadata:\n  opencode/autoinvoke: true\n"))
    _write(tmp_path, ".opencode/skills/old/SKILL.md", base.format(n="old", extra="disable-model-invocation: true\n"))
    rows = _rows(tmp_path, capsys)
    assert ("warn", "description-no-when") in _codes(rows[".opencode/skills/shown"])
    assert rows[".opencode/skills/hid"]["disable_model_invocation"] is True
    assert "description-no-when" not in {c for _, c in _codes(rows[".opencode/skills/hid"])}
    assert rows[".opencode/skills/won"]["disable_model_invocation"] is False  # autoinvoke wins when both are set
    assert rows[".opencode/skills/old"]["disable_model_invocation"] is True


def test_a_nameless_skill_warns_under_a_spec_host_and_not_under_opencode(tmp_path, capsys):
    body = "---\ndescription: Use when you need quantum widgets.\n---\n"
    _write(tmp_path, ".claude/skills/nameless/SKILL.md", body)
    _write(tmp_path, ".opencode/skills/nameless/SKILL.md", body)
    rows = _rows(tmp_path, capsys)
    assert ("warn", "name-missing") in _codes(rows[".claude/skills/nameless"])
    assert "name-missing" not in {c for _, c in _codes(rows[".opencode/skills/nameless"])}


def test_a_relative_root_keeps_a_spec_host_in_the_spec(tmp_path, monkeypatch):
    path = _write(tmp_path, ".kiro/skills/proj/skills/a/SKILL.md", "---\nname: a\ndescription: x\n---\n")
    monkeypatch.chdir(tmp_path / ".kiro" / "skills" / "proj")
    assert skill_format(Path("skills/a/SKILL.md"), Path("skills")) == FORMAT_AGENT_SKILL
    assert skill_format(path, Path("skills")) == FORMAT_AGENT_SKILL


def test_ignoring_the_opencode_warning_keeps_a_late_frontmatter_file_exempt(tmp_path, capsys):
    _write(tmp_path, ".opencode/skills/x/SKILL.md", "# Instructions\n\n---\nname: example\n---\n\nDo a thing.\n")
    argv = ["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--ignore", "opencode-no-description"]
    assert main(argv) == 0
    assert main([*argv, "--per-skill"]) == 0
