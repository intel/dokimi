# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval skill-coverage``: requirements of a SKILL.md mapped to the eval set's checks (#220, A5).

Every model call goes through an injected transport on :class:`LLMClient`; no test reaches a provider.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import dokimi.skill_coverage as sc
from dokimi.cli import build_parser
from dokimi.novelty.client import LLMClient, SpendCap
from dokimi.utils import InputError


class Transport:
    """A scripted transport: returns the replies in order (the last one repeats) and records each prompt."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt, system):
        self.prompts.append(prompt)
        reply = self.replies[min(len(self.prompts), len(self.replies)) - 1]
        return reply, None


EXTRACT = json.dumps({"requirements": [
    {"section": "triggers", "text": "Activate on questions about flag -x"},
    {"section": "must_hold", "text": "Use the -x flag, never -y"},
    {"section": "must_hold", "text": "Name the default value"},
    {"section": "bogus", "text": "skipped: unknown section"},
    {"section": "failure_modes", "text": "   "},
]})
MAP = json.dumps({"items": {
    "trig-01": ["e1"],
    "mh-01": ["e2", "no_such_check"],
    "mh-02": [],
}})


def _client(*replies: str) -> tuple[LLMClient, Transport]:
    transport = Transport(*replies)
    return LLMClient(model="m", provider="anthropic", role="coverage", transport=transport), transport


def _skill(tmp_path: Path, text: str = "# Skill\nUse the -x flag.\n") -> Path:
    path = tmp_path / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _evals(tmp_path: Path) -> Path:
    path = tmp_path / "evals.json"
    path.write_text(json.dumps({"evals": [
        {"id": "e1", "prompt": "what flag?", "expected_output": "-x", "needs": ["n1"]},
        {"id": "e2", "prompt": "default?", "expected_output": "3"},
    ]}), encoding="utf-8")
    return path


def _task(root: Path, name: str, tests: dict[str, str]) -> None:
    task = root / name
    (task / "tests").mkdir(parents=True)
    (task / "task.toml").write_text('version = "1.0"\n', encoding="utf-8")
    (task / "instruction.md").write_text("Do the thing.\n", encoding="utf-8")
    for fname, body in tests.items():
        (task / "tests" / fname).write_text(body, encoding="utf-8")


# --- reading the skill --------------------------------------------------------

def test_read_skill_takes_a_file_or_a_skill_directory(tmp_path):
    path = _skill(tmp_path)
    by_file = sc.read_skill(path)
    by_dir = sc.read_skill(path.parent)
    assert by_file.path == by_dir.path == str(path)
    assert by_file.name == "demo"
    assert len(by_file.sha256) == 64


def test_read_skill_refuses_a_missing_file_and_an_oversized_one(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="cannot read the skill"):
        sc.read_skill(tmp_path / "nope" / "SKILL.md")
    monkeypatch.setattr(sc, "MAX_SKILL_CHARS", 10)
    with pytest.raises(ValueError, match="over the 10"):
        sc.read_skill(_skill(tmp_path, "x" * 11))


# --- the checks of an eval set ------------------------------------------------

def test_evals_json_rows_are_checks_named_by_id_without_their_needs(tmp_path):
    checks = sc.load_checks(_evals(tmp_path))
    assert [c.name for c in checks] == ["e1", "e2"]
    assert checks[0].units == ("e1",)
    assert "needs" not in checks[0].source and "what flag?" in checks[0].source


def test_a_malformed_evals_file_is_refused(tmp_path):
    bad = tmp_path / "evals.json"
    bad.write_text(json.dumps({"evals": "not a list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a list"):
        sc.load_checks(bad)


def test_duplicate_check_names_are_refused(tmp_path):
    dup = tmp_path / "evals.json"
    dup.write_text(json.dumps({"evals": [{"id": "a"}, {"id": "a"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="two checks are named 'a'"):
        sc.load_checks(dup)


def test_task_test_functions_are_checks_and_a_shared_body_is_one_check(tmp_path):
    shared = "def test_flag():\n    assert True\n"
    root = tmp_path / "tasks"
    _task(root, "t1", {"test_answer.py": shared + "\ndef helper():\n    pass\n"})
    _task(root, "t2", {"test_answer.py": shared})
    _task(root, "t3", {"test_answer.py": "def test_flag():\n    assert False\n"})
    checks = {c.name: c for c in sc.load_checks(root)}
    assert set(checks) == {"test_flag#1", "test_flag#2"}  # two bodies: numbered, not merged
    assert checks["test_flag#1"].units == ("t1", "t2")
    assert checks["test_flag#2"].units == ("t3",)
    assert "helper" not in " ".join(checks)  # only test_* functions are checks


def test_a_single_body_task_check_keeps_the_function_name(tmp_path):
    root = tmp_path / "tasks"
    _task(root, "t1", {"test_answer.py": "def test_only():\n    pass\n"})
    [check] = sc.load_checks(root)
    assert (check.name, check.units) == ("test_only", ("t1",))


def test_a_long_eval_row_keeps_its_expected_output_in_the_check(tmp_path):
    path = tmp_path / "evals.json"
    path.write_text(json.dumps({"evals": [
        {"id": "e1", "prompt": "p" * 1600, "expected_output": "THE-EXPECTED-ANSWER"}]}), encoding="utf-8")
    [check] = sc.load_checks(path)
    assert "THE-EXPECTED-ANSWER" in check.source  # the grading criterion is never clipped away
    assert "[clipped]" not in check.source


def test_a_parametrized_task_check_keeps_its_decorators_and_stays_apart(tmp_path):
    root = tmp_path / "tasks"
    for task, flag in (("t1", "-x"), ("t2", "-y")):
        _task(root, task, {"test_answer.py": (
            "import pytest\n\n"
            f'@pytest.mark.parametrize("flag,answer", [("{flag}", "a")])\n'
            "def test_flag(flag, answer):\n    assert flag == answer\n")})
    checks = sc.load_checks(root)
    assert sorted(c.name for c in checks) == ["test_flag#1", "test_flag#2"]  # two checks, not one merged
    assert any('("-x", "a")' in c.source for c in checks)  # the values the check depends on reach the mapper
    assert any('("-y", "a")' in c.source for c in checks)


def test_task_checks_that_share_a_long_prefix_stay_apart_with_their_full_source(tmp_path):
    prefix = "".join(f"    x{i} = {i}\n" for i in range(300))  # longer than the old per-check clip
    root = tmp_path / "tasks"
    _task(root, "t1", {"test_answer.py": f"def test_same(x):\n{prefix}    assert x == 1\n"})
    _task(root, "t2", {"test_answer.py": f"def test_same(x):\n{prefix}    assert x == 2\n"})
    checks = {c.name: c for c in sc.load_checks(root)}
    assert set(checks) == {"test_same#1", "test_same#2"}
    assert any("assert x == 2" in c.source for c in checks.values())


def test_a_form_feed_or_unicode_line_separator_above_a_test_does_not_shift_its_source(tmp_path):
    root = tmp_path / "tasks"
    for task, answer in (("t1", "1"), ("t2", "2")):  # str.splitlines breaks on \x0c and \u2028; ast does not
        _task(root, task, {"test_answer.py": f"x = 0  # \x0c\u2028\n\ndef test_a(x):\n    assert x == {answer}\n"})
    sources = sorted(c.source for c in sc.load_checks(root))
    assert sources == ["def test_a(x):\n    assert x == 1", "def test_a(x):\n    assert x == 2"]


# --- the two model calls ------------------------------------------------------

def test_extract_requirements_numbers_by_section_and_skips_bad_rows(tmp_path):
    client, transport = _client(EXTRACT)
    reqs, dropped = sc.extract_requirements(sc.read_skill(_skill(tmp_path)), client)
    assert [(r.id, r.section) for r in reqs] == [
        ("trig-01", "triggers"), ("mh-01", "must_hold"), ("mh-02", "must_hold")]
    assert dropped == 0
    assert "Use the -x flag." in transport.prompts[0]  # the SKILL.md text is in the prompt


def test_extract_requirements_caps_the_list_and_counts_what_it_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MAX_REQUIREMENTS", 2)
    client, _ = _client(EXTRACT)
    reqs, dropped = sc.extract_requirements(sc.read_skill(_skill(tmp_path)), client)
    assert len(reqs) == 2
    assert dropped == 1  # the blank row is skipped, not counted; the third valid row is cut


def test_an_unusable_requirement_reply_is_a_run_failure(tmp_path):
    client, transport = _client("not json at all")
    with pytest.raises(sc.SkillCoverageError, match="no usable requirement list"):
        sc.extract_requirements(sc.read_skill(_skill(tmp_path)), client)
    assert len(transport.prompts) == 1  # one call: a bad reply is reported, not retried here


def test_a_requirement_object_with_no_usable_rows_is_a_run_failure(tmp_path):
    client, _ = _client('{"requirements": [{"section": "nope", "text": "x"}]}')
    with pytest.raises(sc.SkillCoverageError, match="no usable requirement list"):
        sc.extract_requirements(sc.read_skill(_skill(tmp_path)), client)


def test_map_requirements_keeps_known_names_and_counts_the_rest(tmp_path):
    checks = sc.load_checks(_evals(tmp_path))
    reqs = [sc.Requirement("trig-01", "triggers", "a"), sc.Requirement("mh-01", "must_hold", "b"),
            sc.Requirement("mh-02", "must_hold", "c")]
    client, _ = _client(MAP)
    links = sc.map_requirements(reqs, checks, client)
    assert links.by_requirement == {"trig-01": ("e1",), "mh-01": ("e2",), "mh-02": ()}
    assert links.dropped_names == 1  # no_such_check


def test_map_requirements_refuses_a_catalogue_over_budget_before_any_call(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MAX_CATALOGUE_CHARS", 20)
    client, transport = _client(MAP)
    with pytest.raises(ValueError, match="one mapping call takes"):
        sc.map_requirements([sc.Requirement("mh-01", "must_hold", "b")], sc.load_checks(_evals(tmp_path)), client)
    assert transport.prompts == []


def test_an_unusable_mapping_reply_is_a_run_failure(tmp_path):
    client, _ = _client('{"links": {}}')
    with pytest.raises(sc.SkillCoverageError, match="no usable mapping"):
        sc.map_requirements([sc.Requirement("mh-01", "must_hold", "b")], sc.load_checks(_evals(tmp_path)), client)


# --- the report ---------------------------------------------------------------

def _report(tmp_path: Path) -> dict:
    skill = sc.read_skill(_skill(tmp_path))
    checks = sc.load_checks(_evals(tmp_path))
    client, _ = _client(EXTRACT, MAP)
    reqs, dropped = sc.extract_requirements(skill, client)
    links = sc.map_requirements(reqs, checks, client)
    return sc.coverage_report(skill, reqs, checks, links, evals_root="evals.json", model="m",
                              provider="anthropic", cost={"model": "m"}, requirements_dropped=dropped)


def test_report_marks_covered_and_candidate_requirements(tmp_path):
    report = _report(tmp_path)
    assert report["schema_version"] == "skill_coverage.v1"
    assert report["method"] == "llm_static_map"
    assert "not a verified coverage score" in report["label"]
    by_id = {r["id"]: r for r in report["requirements"]}
    assert by_id["trig-01"]["covered"] and by_id["trig-01"]["units"] == ["e1"]
    assert by_id["mh-01"]["checks"] == ["e2"]
    assert [c["id"] for c in report["candidate_checks"]] == ["mh-02"]
    s = report["summary"]
    assert (s["requirements"], s["covered"], s["candidate_checks"], s["checks"]) == (3, 2, 1, 2)
    assert s["by_section"]["must_hold"] == {"covered": 1, "total": 2}
    assert s["check_names_dropped"] == 1


def test_render_names_the_label_the_candidates_and_escapes_pipes(tmp_path):
    report = _report(tmp_path)
    report["requirements"][0]["text"] = "a | b"
    text = sc.render_markdown(report)
    assert text.startswith("## Skill coverage: demo (candidate checks)")
    assert "not a verified coverage score" in text
    assert "a \\| b" in text
    assert "- mh-02 [must_hold] Name the default value" in text
    assert "1 check name(s) the eval set does not have" in text
    assert "past the cap" not in text  # nothing was cut at the requirement cap


# --- the command --------------------------------------------------------------

def _run(monkeypatch, argv, *replies: str):
    """Run ``eval skill-coverage`` through the real parser, with the model replaced by a scripted transport."""
    from dokimi.commands import skill_coverage as cmd

    transport = Transport(*replies)
    monkeypatch.setattr(cmd, "_client", lambda args: LLMClient(
        model=args.model[1], provider=args.model[0], role="coverage", transport=transport,
        cap=SpendCap(limit_usd=args.max_usd) if args.max_usd is not None else None))
    args = build_parser().parse_args(["eval", "skill-coverage", *argv])
    return args.func(args), transport


def test_command_prints_the_table_and_writes_a_valid_artifact(tmp_path, monkeypatch, capsys):
    jsonschema = pytest.importorskip("jsonschema")
    out = tmp_path / "coverage.json"
    _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path)), "--out", str(out)], EXTRACT, MAP)
    printed = capsys.readouterr().out
    assert "## Skill coverage: demo (candidate checks)" in printed
    artifact = json.loads(out.read_text(encoding="utf-8"))
    schema = json.loads((Path(sc.__file__).parent / "schemas" / "skill_coverage.v1.json").read_text(encoding="utf-8"))
    jsonschema.validate(artifact, schema)
    assert artifact["model"] == {"model": "claude-sonnet-5-5", "provider": "anthropic"}


def test_command_default_model_is_the_judge_default(monkeypatch, tmp_path):
    from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER

    args = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path))])
    assert args.model == (DEFAULT_JUDGE_PROVIDER, DEFAULT_JUDGE_MODEL)


def test_command_refuses_an_unknown_provider_and_a_zero_cap(tmp_path):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["eval", "skill-coverage", "s", "e", "--model", "nope/x"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["eval", "skill-coverage", "s", "e", "--max-usd", "0"])


def test_command_refuses_a_missing_evals_path_as_input_error(tmp_path, monkeypatch):
    with pytest.raises(InputError, match="does not exist"):
        _run(monkeypatch, [str(_skill(tmp_path)), str(tmp_path / "nope.json")])


def test_command_refuses_an_out_that_would_overwrite_the_skill(tmp_path, monkeypatch):
    skill = _skill(tmp_path)
    with pytest.raises(InputError, match="would overwrite an input"):
        _run(monkeypatch, [str(skill), str(_evals(tmp_path)), "--out", str(skill)], EXTRACT, MAP)
    assert skill.read_text(encoding="utf-8").startswith("# Skill")


def test_command_exits_1_on_an_unusable_model_reply(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path))], "garbage")
    assert exc.value.code == 1
    assert "no usable requirement list" in capsys.readouterr().err


def test_command_exits_1_when_the_usd_cap_is_reached(tmp_path, monkeypatch, capsys):
    from dokimi.novelty.client import SpendCapReached

    def boom(self, model):
        raise SpendCapReached("cap")

    monkeypatch.setattr(SpendCap, "before_call", boom)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path)), "--max-usd", "0.5"], EXTRACT, MAP)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--max-usd cap was reached" in err
    assert "before the map was made" not in err  # the cap can stop the run after the first call, too


def test_command_refuses_an_out_over_the_skill_md_read_from_a_skill_directory(tmp_path, monkeypatch):
    skill = _skill(tmp_path)
    with pytest.raises(InputError, match="would overwrite an input"):
        _run(monkeypatch, [str(skill.parent), str(_evals(tmp_path)), "--out", str(skill)], EXTRACT, MAP)
    assert skill.read_text(encoding="utf-8").startswith("# Skill")


def test_command_refuses_an_out_over_a_task_check_file_before_any_call(tmp_path, monkeypatch):
    root = tmp_path / "tasks"
    _task(root, "t1", {"test_answer.py": "def test_flag():\n    assert True\n"})
    check = root / "t1" / "tests" / "test_answer.py"
    with pytest.raises(InputError, match="would overwrite an input"):
        _run(monkeypatch, [str(_skill(tmp_path)), str(root), "--out", str(check)], EXTRACT, MAP)
    assert check.read_text(encoding="utf-8").startswith("def test_flag")


def test_the_real_client_carries_the_max_usd_cap_and_no_cap_without_it(tmp_path):
    from dokimi.commands import skill_coverage as cmd

    capped = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path)),
                                        "--max-usd", "0.5"])
    client = cmd._client(capped)  # the client the command makes, not a test stand-in
    assert client.cap is not None and client.cap.limit_usd == 0.5
    plain = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path))])
    assert cmd._client(plain).cap is None


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0"])
def test_max_usd_refuses_a_value_that_would_disable_the_cap(tmp_path, value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path)),
                                   "--max-usd", value])


def _no_paid_call(monkeypatch):
    from dokimi.commands import skill_coverage as cmd

    def no_client(args):
        raise AssertionError("a paid call was made before the input check")

    monkeypatch.setattr(cmd, "_client", no_client)


def test_an_empty_eval_set_is_refused_before_any_paid_call(tmp_path, monkeypatch):
    _no_paid_call(monkeypatch)
    empty = tmp_path / "evals.json"
    empty.write_text(json.dumps({"evals": []}), encoding="utf-8")
    args = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(empty)])
    with pytest.raises(InputError, match="no evals.json rows"):
        args.func(args)


def test_an_over_budget_eval_set_is_refused_before_any_paid_call(tmp_path, monkeypatch):
    _no_paid_call(monkeypatch)
    monkeypatch.setattr(sc, "MAX_CATALOGUE_CHARS", 20)
    args = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path))])
    with pytest.raises(InputError, match="one mapping call takes"):
        args.func(args)


def test_an_out_equal_to_the_evals_file_is_refused_before_any_paid_call(tmp_path, monkeypatch):
    _no_paid_call(monkeypatch)
    evals = _evals(tmp_path)
    args = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(evals), "--out", str(evals)])
    with pytest.raises(InputError, match="would overwrite an input"):
        args.func(args)
    assert json.loads(evals.read_text(encoding="utf-8"))["evals"][0]["id"] == "e1"


def test_an_out_in_a_missing_folder_says_the_folder_is_missing(tmp_path, monkeypatch):
    _no_paid_call(monkeypatch)
    out = tmp_path / "results" / "new" / "cov.json"
    args = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path)),
                                      "--out", str(out)])
    with pytest.raises(InputError, match="its folder .* does not exist"):
        args.func(args)
    assert not out.parent.exists()  # refused, not created


def test_command_refuses_an_out_that_is_a_directory_before_any_paid_call(tmp_path, monkeypatch):
    from dokimi.commands import skill_coverage as cmd

    def no_client(args):
        raise AssertionError("a paid call was made before the --out check")

    monkeypatch.setattr(cmd, "_client", no_client)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(InputError, match="cannot write"):
        _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path)), "--out", str(out_dir)])


def test_a_requirement_with_null_or_non_string_text_is_not_usable(tmp_path):
    reply = json.dumps({"requirements": [
        {"section": "must_hold", "text": None},
        {"section": "must_hold", "text": 3},
        {"section": "must_hold", "text": "Use the -x flag"},
    ]})
    client, _ = _client(reply)
    reqs, dropped = sc.extract_requirements(sc.read_skill(_skill(tmp_path)), client)
    assert [r.text for r in reqs] == ["Use the -x flag"]
    assert dropped == 0
    client, _ = _client('{"requirements": [{"section": "must_hold", "text": null}]}')
    with pytest.raises(sc.SkillCoverageError, match="no usable requirement list"):
        sc.extract_requirements(sc.read_skill(_skill(tmp_path)), client)


def test_read_skill_names_the_skill_for_a_relative_file_and_a_dot_directory(tmp_path, monkeypatch):
    skill = _skill(tmp_path)
    monkeypatch.chdir(skill.parent)
    assert sc.read_skill(Path("SKILL.md")).name == "demo"
    assert sc.read_skill(Path(".")).name == "demo"


def test_a_mapping_reply_that_names_no_requirement_id_is_a_run_failure(tmp_path):
    checks = sc.load_checks(_evals(tmp_path))
    reqs = [sc.Requirement("mh-01", "must_hold", "b"), sc.Requirement("mh-02", "must_hold", "c")]
    for reply in ('{"items": {}}', '{"items": {"mh-1": ["e2"], "mh-2": []}}'):
        client, _ = _client(reply)
        with pytest.raises(sc.SkillCoverageError, match="no usable mapping"):
            sc.map_requirements(reqs, checks, client)


class _Capture:
    """A client that returns a fixed reply and keeps the cache predicate ``map_requirements`` passed in."""

    def __init__(self, reply: dict):
        self.reply = reply
        self.usable = None

    def json_object(self, prompt, *, system=None, usable=None):
        self.usable = usable
        return self.reply


def test_the_cache_and_the_guard_refuse_the_same_mappings(tmp_path):
    checks = sc.load_checks(_evals(tmp_path))
    reqs = [sc.Requirement("mh-01", "must_hold", "b"), sc.Requirement("mh-02", "must_hold", "c")]
    unusable = ('{"items": {}}', '{"items": {"zz": ["e1"]}}', '{"items": {"mh-01": null}}',
                '{"items": {"mh-01": "e2"}}', '{"items": {"mh-01": ["e2"], "mh-02": null}}')
    for reply in unusable:
        fake = _Capture(json.loads(reply))
        with pytest.raises(sc.SkillCoverageError, match="no usable mapping"):
            sc.map_requirements(reqs, checks, fake)
        assert not fake.usable(json.loads(reply)), reply  # never cached, so an identical rerun asks again


def test_an_explicit_empty_list_is_a_usable_mapping(tmp_path):
    checks = sc.load_checks(_evals(tmp_path))
    reqs = [sc.Requirement("mh-01", "must_hold", "b"), sc.Requirement("mh-02", "must_hold", "c")]
    fake = _Capture({"items": {"mh-01": [], "mh-02": ["e1"]}})
    links = sc.map_requirements(reqs, checks, fake)
    assert fake.usable({"items": {"mh-01": []}})
    assert links.by_requirement == {"mh-01": (), "mh-02": ("e1",)}


def test_names_filed_under_an_unknown_requirement_id_are_dropped_and_counted(tmp_path):
    checks = sc.load_checks(_evals(tmp_path))
    reqs = [sc.Requirement("mh-01", "must_hold", "b")]
    client, _ = _client('{"items": {"mh-01": ["e2"], "mh-99": ["e1", "no_such_check"]}}')
    links = sc.map_requirements(reqs, checks, client)
    assert links.by_requirement == {"mh-01": ("e2",)}
    assert links.dropped_names == 2


def test_command_refuses_a_dangling_symlink_as_out_before_any_paid_call(tmp_path, monkeypatch):
    from dokimi.commands import skill_coverage as cmd

    def no_client(args):
        raise AssertionError("a paid call was made before the --out check")

    monkeypatch.setattr(cmd, "_client", no_client)
    link = tmp_path / "coverage.json"
    link.symlink_to(tmp_path / "missing" / "coverage.json")
    with pytest.raises(InputError, match="cannot write"):
        _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path)), "--out", str(link)])


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes into a read-only folder")
def test_command_refuses_a_symlink_into_a_read_only_folder_as_out_before_any_paid_call(tmp_path, monkeypatch):
    from dokimi.commands import skill_coverage as cmd

    def no_client(args):
        raise AssertionError("a paid call was made before the --out check")

    monkeypatch.setattr(cmd, "_client", no_client)
    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked / "coverage.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "coverage.json"
    link.symlink_to(target)
    locked.chmod(0o555)
    try:
        with pytest.raises(InputError, match="cannot write"):
            _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path)), "--out", str(link)])
    finally:
        locked.chmod(0o755)


def test_a_dangling_symlink_into_a_writable_folder_is_written_through_as_out(tmp_path, monkeypatch):
    target = tmp_path / "later.json"
    link = tmp_path / "coverage.json"
    link.symlink_to(target)  # save_artifact creates the target: the run is not refused
    _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path)), "--out", str(link)], EXTRACT, MAP)
    assert json.loads(target.read_text(encoding="utf-8"))["skill"]["name"] == "demo"


def test_command_refuses_an_out_that_cannot_be_resolved_before_any_paid_call(tmp_path, monkeypatch):
    # Python 3.11/3.12 raise RuntimeError on a symlink loop; 3.13 does not, so the failure is simulated here.
    _no_paid_call(monkeypatch)
    out = tmp_path / "coverage.json"
    real_resolve = Path.resolve

    def loop_on_out(self, *args, **kwargs):
        if self == out:
            raise RuntimeError("Symlink loop from 'coverage.json'")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", loop_on_out)
    args = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path)),
                                      "--out", str(out)])
    with pytest.raises(InputError, match="cannot resolve .*Symlink loop"):
        args.func(args)


def test_command_refuses_an_out_when_the_eval_inputs_cannot_be_listed_before_any_paid_call(tmp_path, monkeypatch):
    from dokimi import eval_coverage

    _no_paid_call(monkeypatch)

    def unreadable(evals):
        raise OSError("unreadable check folder")

    monkeypatch.setattr(eval_coverage, "input_files", unreadable)
    out = tmp_path / "coverage.json"
    args = build_parser().parse_args(["eval", "skill-coverage", str(_skill(tmp_path)), str(_evals(tmp_path)),
                                      "--out", str(out)])
    with pytest.raises(InputError, match="cannot resolve the eval inputs"):
        args.func(args)
    assert not out.exists()


def test_command_reports_a_failed_artifact_write_as_input_error(tmp_path, monkeypatch):
    from dokimi import artifacts

    def full_disk(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(artifacts, "save_artifact", full_disk)
    out = tmp_path / "coverage.json"
    with pytest.raises(InputError, match="cannot write .*No space left on device"):
        _run(monkeypatch, [str(_skill(tmp_path)), str(_evals(tmp_path)), "--out", str(out)], EXTRACT, MAP)
    assert not out.exists()
