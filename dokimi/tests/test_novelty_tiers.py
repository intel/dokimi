# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Cost tiers: what each one gives up, who wins over whom, and the call projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.tiers import (
    DEFAULT_SETTINGS,
    FLAG_DEFAULTS,
    FORFEITS,
    SETTING_NAMES,
    TIERS,
    RunSettings,
    estimate_calls,
    resolve_settings,
)


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _flags(args):
    return {flag: getattr(args, flag) for flag in FLAG_DEFAULTS}


def test_the_recorded_flag_defaults_match_the_parser():
    """If a CLI default moves, the override detection silently breaks — fail here instead."""
    parsed = _parse("novelty", "analyze", "--artifact", "skill:x")
    for flag, default in FLAG_DEFAULTS.items():
        assert getattr(parsed, flag) == default, flag


def test_no_tier_leaves_the_historical_defaults_alone():
    resolved = resolve_settings(
        None, _flags(_parse("novelty", "analyze", "--artifact", "skill:x")))
    assert resolved == RunSettings(values=DEFAULT_SETTINGS)
    assert resolved.tier_record() is None


def test_every_tier_names_what_it_forfeits():
    for name, spec in TIERS.items():
        assert spec["forfeits"], name
        resolved = resolve_settings(
            name, _flags(_parse("novelty", "analyze", "--artifact", "skill:x")))
        assert resolved.tier_record() == {"name": name, "forfeits": spec["forfeits"],
                                          "overrides": {}}


def test_a_cheap_tier_buys_its_saving_from_the_probe_budget():
    flags = _flags(_parse("novelty", "analyze", "--artifact", "skill:x"))
    triage, study = resolve_settings("triage", flags), resolve_settings("study", flags)
    assert triage.values["max_atoms"] < study.values["max_atoms"]
    assert (triage.values["paraphrases"], triage.values["reversals"]) == (0, False)
    assert (study.values["paraphrases"], study.values["reversals"]) == (1, True)
    # The measured shape: triage is ~10x cheaper than study on the same artifact.
    cheap = estimate_calls(25, triage.values)["total_calls"]
    full = estimate_calls(120, study.values)["total_calls"]
    assert full / cheap > 8


def test_an_explicit_flag_wins_over_the_tier_and_is_recorded():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "triage",
                  "--max-atoms", "90")
    resolved = resolve_settings(args.tier, _flags(args))
    assert resolved.values["max_atoms"] == 90
    assert resolved.overrides == {"max_atoms": 90}


def test_all_atoms_lifts_the_tier_cap_as_its_forfeit_says():
    # The `study` forfeit tells the reader to pass --all-atoms for the full sweep.
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "study",
                  "--all-atoms")
    resolved = resolve_settings(args.tier, _flags(args))
    assert resolved.values["max_atoms"] is None
    assert resolved.overrides == {"all_atoms": True}


def test_a_flag_that_agrees_with_the_tier_is_not_recorded_as_an_override():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "triage",
                  "--no-reversal")
    resolved = resolve_settings(args.tier, _flags(args))
    assert resolved.values["reversals"] is False
    assert resolved.overrides == {}


def test_turning_a_stage_back_on_is_not_expressible_so_the_tier_keeps_it_off():
    # The flags are all negations; there is no `--classify`, so a tier that drops a stage
    # can only be overridden towards *less*. The resolution must not invent the other way.
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "triage")
    assert resolve_settings(args.tier, _flags(args)).values["classify_content"] is False


def test_the_projection_counts_every_probe_in_every_arm():
    settings = {"paraphrases": 1, "reversals": True, "verify_faithfulness": True,
                "classify_content": True, "confirm_carriers": True}
    estimate = estimate_calls(81, settings, n_arms=2)
    assert estimate["paired_probes"] == 162          # direct + paraphrase
    assert estimate["reversal_probes"] == 81
    # answer + grade, per arm, for paired and reversal probes alike
    assert estimate["probe_calls"] == (162 + 81) * 2 * 2
    # Counted against a real run of the same artifact: 1,078 calls measured.
    assert 1040 <= estimate["total_calls"] <= 1120


def test_dropping_probe_kinds_is_what_makes_a_run_cheap():
    direct_only = estimate_calls(81, {"paraphrases": 0, "reversals": False})
    everything = estimate_calls(81, {"paraphrases": 1, "reversals": True})
    assert everything["probe_calls"] == 3 * direct_only["probe_calls"]


def test_estimate_only_spends_nothing_and_prints_the_bill(tmp_path, capsys):
    root = tmp_path / "s"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: s\ndescription: d\n---\n\n# S\n\nUse `--accel` on driver 2.1.\n"
        "Do not use it on 1.0.\n", encoding="utf-8")
    args = _parse("novelty", "analyze", "--artifact", f"skill:{root}",
                  "--tier", "study", "--estimate-only")
    args.func(args)                       # no client is constructed, no network touched
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["estimate"]["total_calls"] > 0
    assert payload["tier"]["name"] == "study"
    assert "nothing was measured" in out.err


def test_an_unknown_tier_is_refused_by_the_parser():
    with pytest.raises(SystemExit):
        _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "cheap")


def test_passing_the_default_atom_cap_explicitly_still_beats_the_tier():
    # The sweep case: `--tier triage --max-atoms 120` used to be indistinguishable from
    # `--tier triage`, so the user's number was silently replaced by 25.
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "triage",
                  "--max-atoms", "120")
    resolved = resolve_settings(args.tier, _flags(args))
    assert resolved.values["max_atoms"] == 120
    assert resolved.overrides == {"max_atoms": 120}


def test_an_unset_cap_resolves_to_the_documented_default():
    from dokimi.novelty.tiers import DEFAULT_MAX_ATOMS

    args = _parse("novelty", "analyze", "--artifact", "skill:x")
    assert args.max_atoms is None                       # the sentinel, not 120
    resolved = resolve_settings(args.tier, _flags(args))
    assert resolved.values["max_atoms"] == DEFAULT_MAX_ATOMS
    assert resolved.overrides == {}


def test_every_dropped_stage_appears_in_the_tier_s_forfeits():
    for name, spec in TIERS.items():
        forfeits = " ".join(spec["forfeits"]).lower()
        settings = spec["settings"]
        if not settings.get("shorten_artifact"):
            assert "shortening" in forfeits, name
        if not settings.get("classify_content"):
            assert "categor" in forfeits or "harmful" in forfeits, name
        if not settings.get("confirm_carriers"):
            assert "cited" in forfeits or "span colour" in forfeits, name
        if not settings.get("reversals"):
            assert "reversal" in forfeits, name
        if not settings.get("paraphrases"):
            assert "paraphrase" in forfeits, name


def test_the_projection_reports_the_oracle_as_a_range_not_as_zero():
    settings = dict(TIERS["study"]["settings"], budgets=(0.75, 0.5))
    estimate = estimate_calls(81, settings)
    # The retention oracle escalates only its ambiguous band, so its cost is between zero
    # and one call per checked pair. Reporting only the lower end made the published
    # 1-3% accuracy claim hold for corpora that never escalate and nothing else.
    assert estimate["oracle_pairs"] > 0
    assert estimate["total_calls_if_every_pair_escalates"] > estimate["total_calls"]
    # A tier that scores no shortening candidates and confirms no carriers has no oracle.
    assert estimate_calls(25, TIERS["triage"]["settings"])["oracle_pairs"] == 0


def test_the_atom_cap_default_is_one_value_in_two_places():
    from dokimi.novelty.analyze import AnalyzeConfig
    from dokimi.novelty.tiers import DEFAULT_MAX_ATOMS

    # The sentinel only works if the CLI's resolved default and the library default agree.
    assert AnalyzeConfig(artifact_ref="x").max_atoms == DEFAULT_MAX_ATOMS
    # And so for every setting: a run with no tier and no flag is the library default.
    assert RunSettings.of(AnalyzeConfig(artifact_ref="x")).values == DEFAULT_SETTINGS
    assert sorted(DEFAULT_SETTINGS) == sorted(SETTING_NAMES)


def test_flag_precedence_does_not_depend_on_dict_order():
    both = {"all_atoms": True, "max_atoms": 50}
    assert resolve_settings("study", both).values["max_atoms"] is None
    assert resolve_settings("study", dict(reversed(list(both.items())))).values[
        "max_atoms"] is None


def test_the_faithfulness_check_is_named_where_a_tier_drops_it():
    for name, spec in TIERS.items():
        if not spec["settings"].get("verify_faithfulness"):
            forfeits = " ".join(spec["forfeits"]).lower()
            assert "faithfulness" in forfeits or "distortion" in forfeits, name


def test_forfeits_follow_the_resolved_settings():
    from dokimi.novelty.tiers import forfeits

    sampled = FORFEITS["sampled"][0]
    standard = TIERS["standard"]["settings"]
    # Before extraction the cap is assumed reached; an atom set under it is not sampled.
    assert sampled in forfeits(standard)
    assert sampled not in forfeits(standard, n_atoms=15)
    assert sampled in forfeits(standard, n_atoms=60)
    # --all-atoms drops the sampled-rate forfeit, triage included.
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "triage",
                  "--all-atoms")
    assert sampled not in resolve_settings(args.tier, _flags(args)).forfeits()
    # --no-verify adds the faithfulness forfeit the tier did not have.
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "standard",
                  "--no-verify")
    assert FORFEITS["verify_faithfulness"][0] in resolve_settings(args.tier,
                                                                  _flags(args)).forfeits()
    assert FORFEITS["verify_faithfulness"][0] not in TIERS["standard"]["forfeits"]


def test_all_atoms_does_not_record_max_atoms_override():
    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "triage",
                  "--max-atoms", "10", "--all-atoms")
    resolved = resolve_settings(args.tier, _flags(args))
    assert resolved.values["max_atoms"] is None
    assert resolved.overrides == {"all_atoms": True}


def test_a_run_without_wordings_is_not_reported_as_having_clean_wordings():
    from dokimi.gate.novelty_gate import evaluate_novelty

    from tests.test_novelty_gate import _artifact

    empty = {"n_probes": 16, "by_kind": {}, "malformed_rate": 0.0}
    verdict = evaluate_novelty(_artifact(probes=empty))
    row = next(c for c in verdict.checks if c.name == "probe_wording_fidelity")
    assert row.measured is False
    assert "probe_wording_fidelity" in verdict.not_measured
    # An aggregate that quotes a high rate with no per-kind rows still fails.
    bad = evaluate_novelty(_artifact(probes=dict(empty, malformed_rate=0.9)))
    assert next(c for c in bad.checks if c.name == "probe_wording_fidelity").passed is False


def test_flags_that_drop_the_probe_kinds_warn_without_a_tier(tmp_path):
    from tests.test_novelty_analyze import _analyze

    warnings = _analyze(tmp_path, paraphrases=0, reversals=False).artifact["warnings"]
    assert any("no paraphrase probe" in w for w in warnings)
    assert any("no reversal probe" in w for w in warnings)
    full = _analyze(tmp_path / "f").artifact["warnings"]
    assert not any("no paraphrase probe" in w or "no reversal probe" in w for w in full)


def test_shortening_under_a_reached_cap_says_it_saw_only_the_opening():
    from dokimi.novelty.tiers import forfeits

    on = {"max_atoms": 60, "shorten_artifact": True, "paraphrases": 1, "reversals": True,
          "verify_faithfulness": True, "classify_content": True, "confirm_carriers": True}
    assert any("opening of the artifact" in f for f in forfeits(on, n_atoms=60))
    assert not any("opening of the artifact" in f for f in forfeits(on, n_atoms=10))
    assert not any("opening of the artifact" in f for f in forfeits(dict(on, max_atoms=None)))
    assert not any("opening of the artifact" in f
                   for f in forfeits(dict(on, shorten_artifact=False), n_atoms=60))


def test_the_settings_travel_through_the_config_unchanged():
    from dokimi.novelty.analyze import AnalyzeConfig

    args = _parse("novelty", "analyze", "--artifact", "skill:x", "--tier", "triage",
                  "--max-atoms", "90", "--no-shorten")
    settings = resolve_settings(args.tier, _flags(args))
    config = AnalyzeConfig(artifact_ref="x", **settings.config_fields())
    assert (config.tier, config.tier_overrides, config.max_atoms) == ("triage",
                                                                       {"max_atoms": 90}, 90)
    assert RunSettings.of(config) == settings


def test_every_tier_controlled_flag_is_read_through_the_resolver():
    """The CLI never maps a tier-controlled flag to a setting by hand (#4)."""
    import ast

    from dokimi.commands import novelty as novelty_cmd

    source = Path(novelty_cmd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
           and node.value.id == "args" and node.attr in FLAG_DEFAULTS:
            direct.add(node.attr)
    assert sorted(direct) == []


def test_the_estimate_caps_the_expected_atoms_and_records_the_tier():
    study = resolve_settings("study", {})
    bill = study.estimate(300, budgets=(0.5,), n_arms=3)
    assert (bill["spans"], bill["atom_cap"]) == (300, 120)
    assert bill["estimate"] == estimate_calls(
        120, dict(TIERS["study"]["settings"], budgets=(0.5,)), n_arms=3)
    assert bill["tier"] == {"name": "study", "forfeits": TIERS["study"]["forfeits"],
                            "overrides": {}}
    assert "cap is reached" in bill["caveat"]
    # A short artifact estimates one atom per two spans; no cap, no caveat, no tier.
    sweep = resolve_settings(None, {"all_atoms": True}).estimate(9)
    assert (sweep["atom_cap"], sweep["estimate"]["n_atoms"]) == (None, 4)
    assert (sweep["caveat"], sweep["tier"]) == (None, None)


def test_the_warnings_follow_the_settings_and_the_atom_count():
    standard = resolve_settings("standard", {"no_verify": True})
    warned = standard.warnings(n_atoms=60)
    assert all(w.startswith("tier `standard`") for w in warned)
    assert any(FORFEITS["verify_faithfulness"][0] in w for w in warned)
    assert "overridden by flags (no_verify)" in warned[-1]
    assert not any(FORFEITS["sampled"][0] in w for w in standard.warnings(n_atoms=10))
    # Without a tier only the dropped probe kinds warn; the stages warn in the analyzer.
    bare = resolve_settings(None, {"no_paraphrase": True, "no_classify": True})
    assert bare.warnings() == FORFEITS["paraphrases"]
    assert bare.tier_record() is None


def test_a_tier_on_the_config_and_the_older_tier_argument_record_the_same(tmp_path):
    from tests.test_novelty_analyze import SKILL, _clients

    from dokimi.novelty.analyze import AnalyzeConfig, analyze

    settings = resolve_settings("triage", {"max_atoms": 1})
    base = dict(artifact_ref="skill:x", concurrency=1)
    on_config = analyze(SKILL, AnalyzeConfig(**base, **settings.config_fields()),
                        extractor=_clients()[0], answerer=_clients()[1],
                        judge=_clients()[2]).artifact
    as_argument = analyze(SKILL, AnalyzeConfig(**base, **settings.values),
                          extractor=_clients()[0], answerer=_clients()[1],
                          judge=_clients()[2],
                          tier={"name": "triage", "overrides": {"max_atoms": 1}}).artifact
    assert on_config["run_config"]["tier"] == as_argument["run_config"]["tier"] == (
        settings.tier_record(n_atoms=1))
    assert on_config["warnings"] == as_argument["warnings"]
    assert any("tier `triage` was overridden by flags (max_atoms)" in w
               for w in on_config["warnings"])


#: Six measured runs of the 2026-10-21 skill scan (``out/*/L2`` and ``out/*/L3``): the
#: artifact's spans and tokens, its tier, the atoms its extraction found, and what it counted
#: by role and dollars. Numbers only; no third-party skill text.
MEASURED_RUNS = [
    # name, spans, tokens, tier, atoms, calls by role, usd
    ("brand-guidelines", 44, 458, "standard", 27,
     {"extractor": 156, "answerer": 104, "judge": 104}, 0.666236),
    ("caveman-review", 42, 567, "standard", 23,
     {"extractor": 172, "answerer": 90, "judge": 90}, 0.820205),
    ("slides", 26, 355, "standard", 13,
     {"extractor": 86, "answerer": 52, "judge": 52}, 0.375282),
    ("ponytail-debt", 23, 371, "standard", 18,
     {"extractor": 96, "answerer": 70, "judge": 70}, 0.470331),
    ("rea-tool-design", 7, 198, "standard", 8,
     {"extractor": 34, "answerer": 32, "judge": 32}, 0.164377),
    ("tdd", 27, 846, "triage", 21,
     {"extractor": 3, "answerer": 42, "judge": 42}, 0.163192),
]
_MODELS = {"extractor": ("us.anthropic.claude-sonnet-5-5", "amazon-bedrock"),
           "answerer": ("us.anthropic.claude-haiku-5-5", "amazon-bedrock"),
           "judge": ("us.anthropic.claude-sonnet-5-5", "amazon-bedrock")}


def _measured_estimate(tier, spans, tokens):
    return resolve_settings(tier, {}).estimate(
        spans, n_tokens=tokens, budgets=(0.75, 0.5), n_arms=2)["estimate"]


def test_the_expected_calls_land_within_a_quarter_of_each_measured_run():
    # The old projection (one atom per two spans, no oracle escalations) was 37-69% under all
    # six of these runs; the token rule and the escalation share bring every one within 20%.
    for name, spans, tokens, tier, atoms, by_role, _ in MEASURED_RUNS:
        estimate = _measured_estimate(tier, spans, tokens)
        counted = sum(by_role.values())
        assert abs(estimate["expected_calls"] - counted) <= 0.25 * counted, name
        assert abs(estimate["n_atoms"] - atoms) <= 0.25 * atoms, name


def test_the_expected_calls_sit_between_the_no_escalation_and_the_full_escalation_bounds():
    for name, spans, tokens, tier, _, _, _ in MEASURED_RUNS:
        estimate = _measured_estimate(tier, spans, tokens)
        assert (estimate["total_calls"] <= estimate["expected_calls"]
                <= estimate["total_calls_if_every_pair_escalates"]), name
        assert estimate["expected_calls"] == sum(estimate["by_role"].values()), name


def test_the_atom_count_takes_the_token_rule_when_the_artifact_has_more_tokens_than_spans():
    everything = resolve_settings(None, {"all_atoms": True})
    assert everything.estimate(7)["estimate"]["n_atoms"] == 3          # spans only
    assert everything.estimate(7, n_tokens=198)["estimate"]["n_atoms"] == 7
    # Short spans with many tokens: the span rule was the smaller of the two.
    assert everything.estimate(20, n_tokens=900)["estimate"]["n_atoms"] == 36


def test_the_dollar_profile_reproduces_the_measured_standard_runs():
    # The profile priced at each run's own call counts lands within a quarter of what the
    # run's `cost.usd` recorded. Triage is left out: its extraction calls write whole atom
    # lists, which the flat profile does not model (see `call_tokens`).
    from dokimi.novelty.tiers import estimate_usd

    for name, _, tokens, tier, _, by_role, usd in MEASURED_RUNS:
        if tier != "standard":
            continue
        priced = estimate_usd(by_role, tokens, _MODELS)
        assert priced is not None, name
        assert abs(priced - usd) <= 0.25 * usd, name


def test_an_unpriced_model_gives_no_dollar_figure_rather_than_zero():
    from dokimi.novelty.tiers import estimate_usd

    unpriced = {"answerer": ("no-such-model-for-dokimi", "openai")}
    assert estimate_usd({"answerer": 10}, 100, unpriced) is None


def test_estimate_only_prints_the_expected_calls_and_a_dollar_figure(tmp_path, capsys):
    root = tmp_path / "s"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                   encoding="utf-8")
    args = _parse("novelty", "analyze", "--artifact", f"skill:{root}", "--tier", "standard",
                  "--estimate-only")
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["estimate"]["expected_calls"] == sum(payload["estimate"]["by_role"].values())
    assert payload["expected_usd"] > 0  # default models are priced; null would be a regression
    assert payload["tokens"] > 0
