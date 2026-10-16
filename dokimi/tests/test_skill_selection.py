# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill selection: the skill-catalog: arm, its scoring, and `catalog trigger-set`."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from dokimi.cli import build_parser
from dokimi.eval.agent_runner import _summarize_tool_usage
from dokimi.eval.arm_summary import summarize_skill_selection
from dokimi.external import trigger_set as ts
from dokimi.report.arms_report import render_arms_report
from dokimi.skills import discover_skills
from dokimi.treatments import SkillCatalogTreatment, create_treatment


def _write_skill(root: Path, name: str, description: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: \"{description}\"\n---\n# {name}\nbody\n", encoding="utf-8"
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    _write_skill(root, "linux-perf", "Profile Linux programs with perf: hotspots, IPC, cache misses.")
    _write_skill(root, "onetbb-quickstart", "Parallelize C++ loops with oneTBB parallel_for and reductions.")
    _write_skill(root, "dpnp-io", "Read and write dpnp arrays to files on Intel GPUs.")
    return root


# ── arm ──────────────────────────────────────────────────────────────────
def test_skill_catalog_arm_offers_every_skill(catalog):
    arm = create_treatment(f"skill-catalog:{catalog}")
    assert isinstance(arm, SkillCatalogTreatment)
    cfg = arm.prepare("q", "lib")
    assert [t.skill.name for t in cfg.tools] == ["dpnp-io", "linux-perf", "onetbb-quickstart"]
    assert cfg.metadata["arm_kind"] == "skill_catalog"
    assert cfg.metadata["catalog_size"] == 3
    assert cfg.metadata["catalog_listing_chars"] > 0


def test_skill_catalog_arm_rejects_bad_catalogs(tmp_path, catalog):
    with pytest.raises(ValueError, match="requires a directory"):
        create_treatment("skill-catalog:")
    with pytest.raises(ValueError, match="not a directory"):
        create_treatment(f"skill-catalog:{tmp_path / 'missing'}")
    with pytest.raises(ValueError, match="empty"):
        SkillCatalogTreatment([])
    skills = discover_skills(catalog)
    with pytest.raises(ValueError, match="duplicate tool names: view_skill_linux_perf"):
        SkillCatalogTreatment(skills + [s for s in skills if s.name == "linux-perf"])


def test_tool_usage_records_first_loaded_skill(catalog):
    tools = create_treatment(f"skill-catalog:{catalog}").prepare("q", "lib").tools
    by_skill = {t.skill.name: t.name for t in tools}
    transcript = [
        {"iteration": 1, "tool": by_skill["onetbb-quickstart"]},
        {"iteration": 2, "tool": by_skill["linux-perf"]},
        {"iteration": 3, "tool": by_skill["onetbb-quickstart"]},
    ]
    skill = _summarize_tool_usage(tools, transcript)["skill"]
    assert skill["selected"] == "onetbb-quickstart"
    assert skill["loaded"] == ["onetbb-quickstart", "linux-perf"]
    assert skill["offered_count"] == 3
    assert _summarize_tool_usage(tools, [])["skill"]["selected"] is None


# ── scoring ──────────────────────────────────────────────────────────────
def _rec(expected, selected, arm="cat", **extra):
    rec = {"question_id": "q1",
           "arms": {arm: {"agentic": True, "tool_usage": {"skill": {"offered": True, "selected": selected}}}}}
    if expected != "UNLABELLED":
        rec["expected_skill"] = expected
    rec.update(extra)
    return rec


def test_selection_summary_scores_hits_misroutes_and_false_triggers():
    records = [
        _rec("linux-perf", "linux-perf"),
        _rec("linux-perf", "onetbb-quickstart"),
        _rec("linux-perf", None),
        _rec(["dpnp-io", "dpnp-memory"], "dpnp-memory"),
        _rec(None, None),
        _rec(None, "linux-perf"),
        _rec("UNLABELLED", "linux-perf"),
    ]
    s = summarize_skill_selection(records)["cat"]
    assert (s["n"], s["n_positive"], s["n_negative"]) == (6, 4, 2)
    assert s["recall"] == 0.5
    assert s["misroute_rate"] == 0.25
    assert s["false_trigger_rate"] == 0.5
    assert s["selection_accuracy"] == round(3 / 6, 3)
    assert s["confusion"]["linux-perf"] == {"linux-perf": 1, "onetbb-quickstart": 1, "(none)": 1}
    assert s["confusion"]["(none)"] == {"(none)": 1, "linux-perf": 1}


def test_selection_summary_skips_arms_without_skills():
    rec = {"question_id": "q1", "expected_skill": "x", "arms": {"baseline": {"answer": "a"}}}
    assert summarize_skill_selection([rec]) == {}


def test_report_renders_selection_table():
    md = render_arms_report({
        "arms": ["cat"], "answers": [],
        "skill_selection_summary": summarize_skill_selection([_rec("a", "a"), _rec(None, "a")]),
    })
    assert "## Skill selection" in md
    assert "| `cat` | 50% | 100% | 100% | 0% | 0% | 100% | 1/1 | 2 | 0 |" in md


# ── trigger set ──────────────────────────────────────────────────────────
def test_resolve_label_rules():
    q = {"metadata": "{'skill_under_test': 'linux-perf'}", "tags": {"skills": ["a", "b"]}}
    assert ts.resolve_label(q, "@metadata.skill_under_test") == "linux-perf"
    assert ts.resolve_label(q, "@tags.skills") == ["a", "b"]
    assert ts.resolve_label({"metadata": {"skill_under_test": None}}, "@metadata.skill_under_test") is None
    with pytest.raises(ts.LabelUnresolved):
        ts.resolve_label(q, "@metadata.missing")
    with pytest.raises(ts.LabelUnresolved):
        ts.resolve_label({"metadata": "not a literal {"}, "@metadata.skill_under_test")
    assert ts.resolve_label(q, "none") is None
    assert ts.resolve_label(q, "onetbb-quickstart") == "onetbb-quickstart"


def test_lint_flags_leaks_echo_framing_and_unknown_labels(catalog):
    skills = discover_skills(catalog)
    qs = ts.build_trigger_set([([
        {"id": "a", "text": "Scenario: use linux-perf to find hotspots. Describe how."},
        {"id": "b", "text": "my loop is slow, why?"},
        {"id": "c", "text": "anything"},
    ], "linux-perf"), ([{"id": "d", "text": "x"}], "no-such-skill")], skills)
    a, b, c, d = (q.lint for q in qs)
    assert a["names_skill"] == ["linux-perf"] and a["exam_framing"]
    assert "hotspots" in a["echo_terms"] and a["lexical_rank"] == 1
    assert not b["names_skill"] and not b["exam_framing"] and not b["echo_terms"]
    assert d["unknown_expected"] == ["no-such-skill"]
    summary = ts.lint_summary(qs)
    assert summary["names_skill"] == 1 and summary["exam_framing"] == 1
    assert summary["unknown_expected"] == ["no-such-skill"]


def test_lexical_rank_does_not_call_a_tie_easy(catalog):
    # dpnp-io sorts first in the catalog; zero overlap with every skill must not
    # make it "lexically easy".
    qs = ts.build_trigger_set([([{"id": "z", "text": "my cat is sad today"}], "dpnp-io")],
                              discover_skills(catalog))
    assert qs[0].lint["lexical_rank"] == 3
    assert ts.lint_summary(qs)["lexically_easy"] == 0


def test_rewrite_sees_only_question_text_and_is_relinted(catalog):
    seen = []

    def rewrite(text):
        seen.append(text)
        return "  why is my parallel_for slow  "

    qs = ts.build_trigger_set([([{"id": "q1", "text": "Scenario: Describe how."}], "onetbb-quickstart")],
                              discover_skills(catalog), rewrite=rewrite)
    assert seen == ["Scenario: Describe how."]
    assert qs[0].text == "why is my parallel_for slow" and qs[0].source_text == "Scenario: Describe how."
    assert not qs[0].lint["exam_framing"]


def test_duplicate_ids_across_sources_rejected(catalog):
    with pytest.raises(ValueError, match="duplicate question id"):
        ts.build_trigger_set([([{"id": 1, "text": "a"}], "none"), ([{"id": 1, "text": "b"}], "none")],
                             discover_skills(catalog))


def test_writers(catalog):
    qs = ts.build_trigger_set([
        ([{"id": "p", "text": "profile it"}], "linux-perf"),
        ([{"id": "f", "text": "fam", "skills": ["dpnp-io", "dpnp-memory"]}], "@skills"),
        ([{"id": "n", "text": "unrelated"}], "none"),
    ], discover_skills(catalog))
    reach = ts.to_reach(qs, catalog_id="c")
    assert reach["queries"][0] == {"id": "p", "text": "profile it", "expected_skill": "linux-perf"}
    assert reach["queries"][1]["acceptable_skills"] == ["dpnp-memory"]
    assert reach["queries"][2] == {"id": "n", "text": "unrelated", "expected_skill": None, "kind": "out_of_scope"}
    assert ts.to_skill_creator(qs, "linux-perf") == [
        {"query": "profile it", "should_trigger": True},
        {"query": "fam", "should_trigger": False},
        {"query": "unrelated", "should_trigger": False},
    ]
    rows = ts.to_questions(qs)["questions"]
    assert rows[2]["expected_skill"] is None and "trigger_lint" in rows[0]


def _run_cli(argv):
    args = build_parser().parse_args(argv)
    args.func(args)


def test_trigger_set_cli(tmp_path, catalog, capsys):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"questions": [
        {"id": "1", "text": "Scenario: perf shows low IPC", "metadata": {"skill_under_test": "linux-perf"}},
        {"id": "2", "text": "fix my oneDAL kmeans", "metadata": {"skill_under_test": None}},
    ]}), encoding="utf-8")
    out = tmp_path / "set.json"
    _run_cli(["catalog", "trigger-set", "--catalog", str(catalog),
              "--questions", f"{qfile}=@metadata.skill_under_test", "--out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [q["expected_skill"] for q in data["queries"]] == ["linux-perf", None]
    assert '"exam_framing": 1' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        _run_cli(["catalog", "trigger-set", "--catalog", str(catalog), "--strict",
                  "--questions", f"{qfile}=@metadata.skill_under_test", "--out", str(out)])
    with pytest.raises(SystemExit, match="PATH=LABEL"):
        _run_cli(["catalog", "trigger-set", "--catalog", str(catalog),
                  "--questions", str(qfile), "--out", str(out)])
    with pytest.raises(SystemExit, match="--target"):
        _run_cli(["catalog", "trigger-set", "--catalog", str(catalog), "--format", "skill-creator",
                  "--questions", f"{qfile}=none", "--out", str(out)])


def test_unresolved_label_is_unlabelled_not_negative(catalog):
    qs = ts.build_trigger_set([([
        {"id": "a", "text": "x", "metadata": {"skill_under_test": "linux-perf"}},
        {"id": "b", "text": "y", "metadata": {}},
    ], "@metadata.skill_under_test")], discover_skills(catalog))
    assert [q.labelled for q in qs] == [True, False]
    assert qs[1].lint["label_unresolved"]
    summary = ts.lint_summary(qs)
    assert (summary["n_positive"], summary["n_negative"], summary["label_unresolved"]) == (1, 0, 1)
    assert [q["id"] for q in ts.to_reach(qs)["queries"]] == ["a"]
    assert len(ts.to_skill_creator(qs, "linux-perf")) == 1
    rows = ts.to_questions(qs)["questions"]
    assert rows[0]["expected_skill"] == "linux-perf" and "expected_skill" not in rows[1]


def test_trigger_set_cli_rejects_a_label_path_that_never_resolves(tmp_path, catalog):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"questions": [
        {"id": "1", "text": "perf shows low IPC", "metadata": {"skill_under_test": "linux-perf"}},
    ]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="resolves for none of 1 questions"):
        _run_cli(["catalog", "trigger-set", "--catalog", str(catalog),
                  "--questions", f"{qfile}=@metadata.skil_under_test", "--out", str(tmp_path / "o.json")])


def test_skill_catalog_rejects_tool_name_collisions_and_long_names(tmp_path):
    root = tmp_path / "c"
    _write_skill(root, "a-b", "first")
    _write_skill(root, "a_b", "second")
    with pytest.raises(ValueError, match="duplicate tool names: view_skill_a_b"):
        SkillCatalogTreatment(discover_skills(root))
    long_root = tmp_path / "l"
    _write_skill(long_root, "x" * 60, "long")
    with pytest.raises(ValueError, match="64-char"):
        SkillCatalogTreatment(discover_skills(long_root))


def test_selection_summary_refuses_a_non_object_arm():
    """No writer writes an arm payload that is not an object or null; it names the record."""
    from dokimi.eval.records import RecordError

    rec = _rec("a", "a")
    rec["arms"]["broken"] = "error string"
    with pytest.raises(RecordError, match=r"answers\[0\]\.arms\.broken: expected an object or null"):
        summarize_skill_selection([rec])


def test_failed_skill_arms_are_counted_not_dropped():
    failed = {"question_id": "q2", "arms": {"cat": {"error": "agent_failed: 429", "answer": None,
                               "metadata": {"arm_kind": "skill_catalog"}}}, "expected_skill": "a"}
    s = summarize_skill_selection([_rec("a", "a")] + [failed] * 9)["cat"]
    assert (s["n"], s["n_failed"], s["recall"]) == (1, 9, 1.0)
    baseline_err = {"question_id": "q3", "arms": {"b": {"error": "x", "metadata": {"arm_kind": "baseline"}}},
                    "expected_skill": "a"}
    assert summarize_skill_selection([baseline_err]) == {}


def test_same_size_catalogs_get_distinct_arm_names(tmp_path):
    for d in ("one", "two"):
        _write_skill(tmp_path / d, "s1", "first")
        _write_skill(tmp_path / d, "s2", "second")
    names = {create_treatment(f"skill-catalog:{tmp_path / d}").name for d in ("one", "two")}
    assert len(names) == 2


def test_lint_word_boundaries_framing_and_acceptable_ties(tmp_path):
    root = tmp_path / "c"
    _write_skill(root, "git", "version control with git")
    _write_skill(root, "perf-a", "profile cycles cache")
    _write_skill(root, "perf-b", "profile cycles cache")
    _write_skill(root, "other", "unrelated words")
    skills = discover_skills(root)
    q = ts.TriggerQuery(id="1", text="My digital filter is slow", expected_skill="perf-a")
    assert ts.lint_query(q, skills)["names_skill"] == []
    q = ts.TriggerQuery(id="2", text="I read it in a blog post but the documentation says otherwise",
                        expected_skill="perf-a")
    assert not ts.lint_query(q, skills)["exam_framing"]
    q = ts.TriggerQuery(id="3", text="profile cycles cache please", expected_skill=["perf-a", "perf-b"])
    assert ts.lint_query(q, skills)["lexical_rank"] == 1


def test_label_from_json_string_metadata():
    q = {"metadata": '{"skill_under_test": "alpha", "ok": true}'}
    assert ts.resolve_label(q, "@metadata.skill_under_test") == "alpha"


def test_trigger_set_cli_paths_with_equals_and_no_write_on_bad_label(tmp_path, catalog):
    d = tmp_path / "run=1"
    d.mkdir()
    qfile = d / "q.json"
    qfile.write_text(json.dumps({"questions": [{"id": "1", "text": "x"}]}), encoding="utf-8")
    out = tmp_path / "o.json"
    _run_cli(["catalog", "trigger-set", "--catalog", str(catalog),
              "--questions", f"{qfile}=linux-perf", "--out", str(out)])
    assert out.exists()
    out.unlink()
    with pytest.raises(SystemExit):
        _run_cli(["catalog", "trigger-set", "--catalog", str(catalog),
                  "--questions", f"{qfile}=linux-pref", "--out", str(out)])
    assert not out.exists()


# ── review round 2: behaviours a mutation would otherwise slip past ─────
def test_arm_runner_copies_label_by_presence_and_emits_summary(monkeypatch, catalog):
    import dokimi.eval.agent_runner as agent_runner_mod
    from dokimi.eval.arm_runner import ArmRunner

    picks = {"pos": "linux-perf", "neg": None, "unl": "linux-perf"}

    def fake_loop(question, tools, **kw):
        return {"answer": "a", "transcript": [], "iterations": 1, "tool_call_count": 0,
                "tool_usage": {"skill": {"offered": True, "selected": picks[question]}},
                "stopped_reason": "answered", "usage": {}, "token_usage": {}, "per_turn": []}

    monkeypatch.setattr(agent_runner_mod, "run_agent_loop", fake_loop)
    runner = ArmRunner([create_treatment(f"skill-catalog:{catalog}")])
    questions = [{"id": "p", "text": "pos", "expected_skill": "linux-perf"},
                 {"id": "n", "text": "neg", "expected_skill": None},
                 {"id": "u", "text": "unl"}]
    records = runner.run("x", questions, concurrency=1)
    by_id = {r["question_id"]: r for r in records}
    assert by_id["n"]["expected_skill"] is None and "expected_skill" not in by_id["u"]
    s = next(iter(runner.build_output("x", records)["skill_selection_summary"].values()))
    assert (s["n"], s["n_positive"], s["n_negative"], s["selection_accuracy"]) == (2, 1, 1, 1.0)


def test_listing_chars_counts_names_and_descriptions(catalog):
    skills = discover_skills(catalog)
    meta = SkillCatalogTreatment(skills).prepare("q", "lib").metadata
    assert meta["catalog_listing_chars"] == sum(len(s.name) + len(s.description) for s in skills)


def test_lexical_rank_takes_best_acceptable_and_empty_list_is_none(tmp_path):
    root = tmp_path / "c"
    _write_skill(root, "aa", "alpha beta gamma")
    _write_skill(root, "bb", "unrelated")
    _write_skill(root, "cc", "beta")
    q = ts.TriggerQuery(id="1", text="alpha beta gamma", expected_skill=["aa", "bb"])
    assert ts.lint_query(q, discover_skills(root))["lexical_rank"] == 1
    assert ts.resolve_label({"skills": []}, "@skills") is None


def test_combined_arm_rejects_duplicate_tools(catalog):
    with pytest.raises(ValueError, match="duplicate tools: view_skill_linux_perf"):
        create_treatment(f"skill-catalog:{catalog}+skill-agent:{catalog / 'linux-perf'}").prepare("q", "lib")


def test_report_renders_dash_for_missing_rates():
    md = render_arms_report({"arms": ["cat"], "answers": [],
                             "skill_selection_summary": summarize_skill_selection([_rec(None, None)])})
    assert "| `cat` | 100% | — | — | — | — | 0% | 0/1 | 1 | 0 |" in md


def _qfile(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text(json.dumps({"questions": rows}), encoding="utf-8")
    return p


def test_trigger_set_cli_checks_each_source_and_inputs(tmp_path, catalog, capsys):
    good = _qfile(tmp_path, "good.json", [{"id": "1", "text": "x", "metadata": {"skill_under_test": "linux-perf"}}])
    typo = _qfile(tmp_path, "typo.json", [{"id": "2", "text": "y", "metadata": {"skill": "linux-perf"}}])
    out = tmp_path / "o.json"
    base = ["catalog", "trigger-set", "--catalog", str(catalog), "--out", str(out)]
    with pytest.raises(SystemExit, match=r"typo\.json=@metadata\.skill_under_test: .* none of 1"):
        _run_cli(base + ["--questions", f"{good}=@metadata.skill_under_test",
                         "--questions", f"{typo}=@metadata.skill_under_test"])
    with pytest.raises(SystemExit, match="--rewrite needs --model"):
        _run_cli(base + ["--questions", f"{good}=none", "--rewrite"])
    with pytest.raises(SystemExit, match="not a directory"):
        _run_cli(["catalog", "trigger-set", "--catalog", str(tmp_path / "nope"),
                  "--questions", f"{good}=none", "--out", str(out)])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"questions": ["just a string"]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="list of question objects"):
        _run_cli(base + ["--questions", f"{bad}=none"])
    named = _qfile(tmp_path, "named.json", [{"id": "3", "text": "use linux-perf here"}])
    with pytest.raises(SystemExit) as exc:
        _run_cli(base + ["--strict", "--questions", f"{named}=linux-perf"])
    assert exc.value.code == 1 and not out.exists()


def test_trigger_set_cli_writes_each_format(tmp_path, catalog, capsys):
    q = _qfile(tmp_path, "q.json", [{"id": "1", "text": "profile it", "skills": ["linux-perf", "dpnp-io"]},
                                    {"id": "2", "text": "unrelated"}])
    out = tmp_path / "o.json"
    base = ["catalog", "trigger-set", "--catalog", str(catalog), "--out", str(out),
            "--questions", f"{q}=@skills"]
    _run_cli(base + ["--format", "skill-creator", "--target", "linux-perf"])
    assert json.loads(out.read_text()) == [{"query": "profile it", "should_trigger": True}]
    _run_cli(base + ["--format", "questions"])
    rows = json.loads(out.read_text())["questions"]
    assert rows[0]["expected_skill"] == ["linux-perf", "dpnp-io"] and "expected_skill" not in rows[1]
    _run_cli(base + ["--format", "reach"])
    assert "skill-reach credits only the first" in capsys.readouterr().out


# ── review round 3 ───────────────────────────────────────────────────────
def _loop_result(answer="a", skill=None):
    return {"answer": answer, "transcript": [], "iterations": 1, "tool_call_count": 0,
            "tool_usage": {"skill": skill or {"offered": True, "selected": None}},
            "stopped_reason": "answered", "usage": {}, "token_usage": {}, "per_turn": []}


class _RaisingCatalog(SkillCatalogTreatment):
    def prepare(self, question_text, library_name, library_id=None):
        raise RuntimeError("catalog unreadable")


def test_combined_and_prepare_failed_arms_count_their_failures(monkeypatch, catalog):
    import dokimi.eval.agent_runner as agent_runner_mod
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.treatments.arms import BaselineTreatment, CombinedTreatment

    def failing_loop(question, tools, **kw):
        raise RuntimeError("bedrock refused")

    monkeypatch.setattr(agent_runner_mod, "run_agent_loop", failing_loop)
    skills = discover_skills(catalog)
    combined = create_treatment(f"skill-agent:{catalog / 'linux-perf'}+skill-agent:{catalog / 'dpnp-io'}")
    broken = _RaisingCatalog(skills, name="broken")
    broken_combo = CombinedTreatment([BaselineTreatment(), _RaisingCatalog(skills)], name="broken_combo")
    runner = ArmRunner([combined, broken, broken_combo])
    records = runner.run("x", [{"id": f"q{i}", "text": "q", "expected_skill": "linux-perf"}
                               for i in range(5)], concurrency=1)
    arm = records[0]["arms"]
    assert arm["broken"]["metadata"] == {"arm_kind": "skill_catalog"}
    assert arm[combined.name]["tool_usage"]["skill"]["offered_count"] == 2
    summary = runner.build_output("x", records)["skill_selection_summary"]
    assert set(summary) == {combined.name, "broken", "broken_combo"}
    assert all((s["n"], s["n_failed"]) == (0, 5) for s in summary.values())


def test_failed_cell_keeps_the_skill_it_had_loaded(monkeypatch, catalog):
    import dokimi.eval.agent_runner as agent_runner_mod
    from dokimi.eval.arm_runner import ArmRunner

    arm = create_treatment(f"skill-catalog:{catalog}")
    tool_name = next(t.name for t in arm.prepare("q", "x").tools if t.skill.name == "linux-perf")
    turns = []

    call = NS(id="c1", type="function", function=NS(name=tool_name, arguments="{}"))

    def fake_completion(messages, model, **kw):
        turns.append(1)
        if len(turns) > 1:
            raise RuntimeError("UnsupportedParamsError")
        return NS(choices=[NS(message=NS(content="", tool_calls=[call]))],
                  usage=NS(prompt_tokens=1, completion_tokens=1, total_tokens=2))

    monkeypatch.setattr(agent_runner_mod, "chat_completion", fake_completion)
    runner = ArmRunner([arm])
    rec = runner.run("x", [{"id": "q", "text": "q", "expected_skill": "linux-perf"}], concurrency=1)[0]
    failed = rec["arms"][arm.name]
    assert failed["error"].startswith("agent_failed")
    assert failed["tool_usage"]["skill"]["loaded"] == ["linux-perf"]
    s = runner.build_output("x", [rec])["skill_selection_summary"][arm.name]
    assert (s["n"], s["n_failed"]) == (0, 1)


def test_first_turn_with_several_loads_is_one_choice(catalog):
    tools = create_treatment(f"skill-catalog:{catalog}").prepare("q", "lib").tools
    by_skill = {t.skill.name: t.name for t in tools}
    transcript = [
        {"iteration": 1, "tool": by_skill["dpnp-io"]},
        {"iteration": 1, "tool": by_skill["linux-perf"]},
        {"iteration": 2, "tool": by_skill["onetbb-quickstart"]},
    ]
    skill = _summarize_tool_usage(tools, transcript)["skill"]
    assert skill["first_turn_loaded"] == ["dpnp-io", "linux-perf"]
    rec = {"question_id": "q1", "expected_skill": "linux-perf", "arms": {"cat": {"tool_usage": {"skill": skill}}}}
    s = summarize_skill_selection([rec])["cat"]
    # The right skill plus a rival is not a hit; the lenient figure keeps it visible.
    assert (s["recall"], s["recall_any"], s["multi_load_rate"]) == (0.0, 1.0, 1.0)
    rec["expected_skill"] = ["dpnp-io", "linux-perf"]  # both acceptable: a hit
    assert summarize_skill_selection([rec])["cat"]["recall"] == 1.0
    rec["expected_skill"] = "onetbb-quickstart"  # loaded later: still a misroute
    assert summarize_skill_selection([rec])["cat"]["misroute_rate"] == 1.0


@pytest.mark.parametrize("label, match", [
    ({"name": "linux-perf"}, "must be a skill name"),
    (["linux-perf", 3], "must be a skill name"),
    ("linux-pref", "names no offered skill: linux-pref"),
])
def test_arms_run_refuses_bad_expected_skill_before_any_call(monkeypatch, tmp_path, catalog,
                                                             capsys, label, match):
    from dokimi.eval.arm_runner import ArmRunner

    def no_calls(*a, **kw):
        raise AssertionError("a call was made before the labels were checked")

    monkeypatch.setattr(ArmRunner, "run", no_calls)
    q = _qfile(tmp_path, "q.json", [{"id": "ok", "text": "a", "expected_skill": None},
                                    {"id": "bad", "text": "b", "expected_skill": label}])
    with pytest.raises(SystemExit):
        _run_cli(["arms", "run", "--product", "x", "--arms", f"skill-catalog:{catalog}",
                  "--questions", str(q), "--out-json", str(tmp_path / "o.json")])
    assert f"question bad: expected_skill {match}" in capsys.readouterr().err


def test_arms_run_accepts_labels_from_any_skill_arm(catalog):
    from dokimi.commands.arms import _validate_expected_skills

    arms = [create_treatment(f"skill-agent:{catalog / 'linux-perf'}+skill-agent:{catalog / 'dpnp-io'}")]
    _validate_expected_skills([{"expected_skill": ["dpnp-io", "linux-perf"]},
                               {"expected_skill": None}], arms)
    # Without a skill arm only the type is checked: the label is not scored.
    _validate_expected_skills([{"expected_skill": "anything"}], [create_treatment("baseline")])


def test_catalog_arm_judge_reference_names_the_catalog_entries(monkeypatch, catalog):
    import dokimi.eval.agent_runner as agent_runner_mod
    from dokimi.eval.arm_runner import ArmRunner

    class _RecordingJudge:
        model, provider = "j", "fake"

        def score_answer(self, library_name, question, answer, context="", ground_truth=None):
            self.context = context
            return {"aggregate": 50}

    loaded = {"offered": True, "loaded": ["linux-perf"], "selected": "linux-perf"}
    monkeypatch.setattr(agent_runner_mod, "run_agent_loop",
                        lambda question, tools, **kw: _loop_result(skill=loaded))
    runner = ArmRunner([create_treatment(f"skill-catalog:{catalog}")])
    judge = _RecordingJudge()
    runner.judge(judge, "x", runner.run("x", [{"id": "q", "text": "q"}], concurrency=1))
    assert judge.context != "(No documentation retrieved)"
    assert "# Skill: linux-perf" in judge.context
    for name in ("onetbb-quickstart", "dpnp-io"):  # offered, never loaded
        assert f"# Skill: {name}" not in judge.context


def test_trigger_set_reads_a_prompt_field(catalog):
    qs = ts.build_trigger_set([([{"id": "p", "prompt": "my loop is slow"}], "linux-perf")],
                              discover_skills(catalog))
    assert qs[0].text == "my loop is slow"


# ── trigger-set CLI: refuse input that would write a silently wrong set ──
def _ts(catalog, out, *extra):
    return ["catalog", "trigger-set", "--catalog", str(catalog), "--out", str(out), *extra]


def test_trigger_set_cli_rejects_an_unknown_skill_creator_target(tmp_path, catalog):
    q = _qfile(tmp_path, "q.json", [{"id": "1", "text": "profile it"}])
    out = tmp_path / "o.json"
    with pytest.raises(SystemExit, match="'linux-perff' is not a skill"):
        _run_cli(_ts(catalog, out, "--questions", f"{q}=linux-perf", "--format", "skill-creator",
                     "--target", "linux-perff"))
    assert not out.exists()


def test_trigger_set_cli_strict_rejects_unresolved_labels(tmp_path, catalog, capsys):
    q = _qfile(tmp_path, "q.json", [{"id": "1", "text": "a", "metadata": {"s": "linux-perf"}},
                                    {"id": "2", "text": "b", "metadata": {}}])
    out = tmp_path / "o.json"
    _run_cli(_ts(catalog, out, "--questions", f"{q}=@metadata.s", "--format", "reach"))
    captured = capsys.readouterr()
    assert "1 questions have an unresolved" in captured.err and "(reach, 1 queries" in captured.out
    out.unlink()
    with pytest.raises(SystemExit) as exc:
        _run_cli(_ts(catalog, out, "--strict", "--questions", f"{q}=@metadata.s"))
    assert exc.value.code == 1 and not out.exists()


def test_trigger_set_cli_rejects_questions_without_text(tmp_path, catalog):
    out = tmp_path / "o.json"
    for bad in ({"id": "1", "title": "t"}, {"id": "1", "text": "  "}, {"id": "1", "text": ["x"]}):
        q = _qfile(tmp_path, "q.json", [bad])
        with pytest.raises(SystemExit, match="question 1 has no text"):
            _run_cli(_ts(catalog, out, "--questions", f"{q}=none"))
        assert not out.exists()


def test_trigger_set_rejects_an_empty_rewrite_and_duplicate_ids_cleanly(tmp_path, catalog):
    with pytest.raises(ValueError, match="rewrite returned empty text for question a"):
        ts.build_trigger_set([([{"id": "a", "text": "x"}], "none")], discover_skills(catalog),
                             rewrite=lambda t: " ")
    q = _qfile(tmp_path, "q.json", [{"id": "1", "text": "a"}, {"id": "1", "text": "b"}])
    with pytest.raises(SystemExit, match="duplicate question id"):
        _run_cli(_ts(catalog, tmp_path / "o.json", "--questions", f"{q}=none"))


def test_catalog_judge_reference_is_the_loaded_skill_not_the_catalog_head(monkeypatch, tmp_path):
    import dokimi.eval.agent_runner as agent_runner_mod
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.eval.judge_context import MAX_JUDGE_REFERENCE_CHARS

    root = tmp_path / "skills"
    for name in ("a-first", "b-second", "z-last"):
        _write_skill(root, name, f"{name} description")
        (root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n" + f"{name} body " * 1500, encoding="utf-8")
        (root / name / "ref.md").write_text("x", encoding="utf-8")

    class _RecordingJudge:
        model, provider = "j", "fake"

        def score_answer(self, library_name, question, answer, context="", ground_truth=None):
            self.context = context
            return {"aggregate": 50}

    def loop(loaded):
        return lambda question, tools, **kw: _loop_result(skill={"offered": True, "loaded": loaded,
                                                                 "selected": (loaded or [None])[0]})

    arm = create_treatment(f"skill-catalog:{root}")
    for loaded, present, absent in ((["z-last"], ["# Skill: z-last"], ["# Skill: a-first"]),
                                    (["a-first", "z-last"], ["# Skill: a-first", "# Skill: z-last"], []),
                                    # Three 12k bodies: a whole-string cut would drop the last.
                                    (["a-first", "b-second", "z-last"],
                                     ["# Skill: a-first", "# Skill: b-second", "# Skill: z-last"], []),
                                    ([], [], ["# Skill:"])):
        monkeypatch.setattr(agent_runner_mod, "run_agent_loop", loop(loaded))
        runner = ArmRunner([arm])
        judge = _RecordingJudge()
        runner.judge(judge, "x", runner.run("x", [{"id": "q", "text": "q"}], concurrency=1))
        assert len(judge.context) <= MAX_JUDGE_REFERENCE_CHARS + 2
        for p in present:
            assert p in judge.context
        for a in absent:
            assert a not in judge.context
        # Every manifest is kept: paths are visible to the agent in the tool schemas.
        assert all(f"skill '{n}'" in judge.context for n in ("a-first", "b-second", "z-last"))

    # Combined with a skill_agent arm: its static reference stays, and the catalog part
    # still follows what was loaded.
    other = tmp_path / "other"
    _write_skill(other, "o-agent", "other description")
    monkeypatch.setattr(agent_runner_mod, "run_agent_loop", loop(["z-last"]))
    runner = ArmRunner([create_treatment(f"skill-agent:{other / 'o-agent'}+skill-catalog:{root}")])
    judge = _RecordingJudge()
    runner.judge(judge, "x", runner.run("x", [{"id": "q", "text": "q"}], concurrency=1))
    assert "# Skill: o-agent" in judge.context and "# Skill: z-last" in judge.context
    assert "# Skill: a-first" not in judge.context


def test_loading_the_whole_catalog_in_one_turn_is_not_a_hit(catalog):
    tools = create_treatment(f"skill-catalog:{catalog}").prepare("q", "lib").tools
    skill = _summarize_tool_usage(tools, [{"iteration": 1, "tool": t.name} for t in tools])["skill"]
    rec = {"question_id": "q1", "expected_skill": "linux-perf", "arms": {"cat": {"tool_usage": {"skill": skill}}}}
    s = summarize_skill_selection([rec])["cat"]
    assert (s["recall"], s["misroute_rate"], s["recall_any"]) == (0.0, 1.0, 1.0)
    assert s["mean_first_turn_loads"] == 3.0
    assert s["confusion"] == {"linux-perf": {"dpnp-io+linux-perf+onetbb-quickstart": 1}}
