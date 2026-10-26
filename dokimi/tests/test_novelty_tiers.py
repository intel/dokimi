# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Cost tiers: what each one gives up, who wins over whom, and the call projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.tiers import (
    CALL_TOKENS,
    DEFAULT_SETTINGS,
    ESCALATION_SHARE,
    EXTRACTOR_TOKENS,
    FITTED_TOKENS_MAX,
    FLAG_DEFAULTS,
    FORFEITS,
    SETTING_NAMES,
    TIERS,
    TOKENS_PER_ATOM,
    RunSettings,
    call_tokens,
    estimate_calls,
    estimate_usd,
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


#: The 25 measured runs of the 2026-10-21 skill scan, split by artifact into a fit half (13
#: runs), which the constants in tiers.py were fitted on, and the held-out half below (12 runs).
#: Each row: the artifact's spans and tokens, its tier, the atoms its extraction found, and the
#: calls by role and the dollars the run recorded. Numbers only: no skill text, no names.
MEASURED_RUNS = [
    ("held-01", 7, 198, "standard", 8, {"extractor": 34, "answerer": 32, "judge": 32}, 0.164377),
    ("held-02", 23, 371, "standard", 18, {"extractor": 96, "answerer": 70, "judge": 70}, 0.470331),
    ("held-03", 42, 567, "standard", 23, {"extractor": 172, "answerer": 90, "judge": 90}, 0.820205),
    ("held-04", 42, 567, "triage", 25, {"extractor": 3, "answerer": 50, "judge": 50}, 0.180702),
    ("held-05", 38, 745, "triage", 25, {"extractor": 3, "answerer": 50, "judge": 50}, 0.183736),
    ("held-06", 61, 883, "triage", 25, {"extractor": 3, "answerer": 50, "judge": 50}, 0.158418),
    ("held-07", 53, 1153, "triage", 25, {"extractor": 3, "answerer": 50, "judge": 50}, 0.206459),
    ("held-08", 66, 1768, "triage", 25, {"extractor": 3, "answerer": 50, "judge": 50}, 0.225759),
    ("held-09", 125, 2156, "triage", 25, {"extractor": 3, "answerer": 50, "judge": 50}, 0.182036),
    ("held-10", 103, 2517, "triage", 25, {"extractor": 4, "answerer": 50, "judge": 50}, 0.245361),
    ("held-11", 274, 3802, "triage", 25, {"extractor": 4, "answerer": 50, "judge": 50}, 0.246153),
    ("held-12", 140, 6723, "triage", 25, {"extractor": 5, "answerer": 50, "judge": 50}, 0.338129),
]

#: The error every projection is held to on the held-out runs, for calls, atoms and dollars.
#: The worst held-out dollar error is 33%; 35% is that figure rounded up.
TOLERANCE = 0.35

_MODELS = {"extractor": ("us.anthropic.claude-sonnet-5-5", "amazon-bedrock"),
           "answerer": ("us.anthropic.claude-haiku-5-5", "amazon-bedrock"),
           "judge": ("us.anthropic.claude-sonnet-5-5", "amazon-bedrock")}
_BUDGETS = (0.75, 0.5)

#: The list prices of the two models in ``_MODELS``, as litellm's bundled price map has them.
#: The dollar checks read the price map, and conftest keeps litellm on its bundled copy, so
#: the map is whichever release is installed: the locked litellm 1.105.0 has no entry for
#: ``us.anthropic.claude-haiku-5-5`` and the answerer priced to None. Pinned here, the checks
#: give the same figures on every release.
_PINNED_LIST_PRICES = {
    "us.anthropic.claude-sonnet-5-5": {"litellm_provider": "bedrock_converse", "mode": "chat",
                                       "input_cost_per_token": 2.2e-06,
                                       "output_cost_per_token": 1.1e-05},
    "us.anthropic.claude-haiku-5-5": {"litellm_provider": "bedrock_converse", "mode": "chat",
                                      "input_cost_per_token": 1.1e-07,
                                      "output_cost_per_token": 5.5e-07},
}


@pytest.fixture
def pinned_list_prices(monkeypatch):
    from litellm import model_cost

    for name, entry in _PINNED_LIST_PRICES.items():
        monkeypatch.setitem(model_cost, name, entry)


def _projection(tier, spans, tokens):
    """The estimate a run at this tier on this artifact prints, and its dollar figure."""
    estimate = resolve_settings(tier, {}).estimate(
        spans, n_tokens=tokens, budgets=_BUDGETS, n_arms=2
    )["estimate"]
    return estimate, estimate_usd(estimate["by_role"], tokens, _MODELS, tier)


def test_the_projection_constants_are_the_fitted_ones():
    # Asserted as values. A change to any of them changes the calibration, and the held-out
    # numbers in docs/skill-novelty.md have to be fitted and restated with it.
    assert TOKENS_PER_ATOM == 23
    assert ESCALATION_SHARE == 0.5
    assert CALL_TOKENS == {"answerer": (-38, 0.789, 284), "judge": (468, 0.004, 89)}
    assert EXTRACTOR_TOKENS == {"triage": (971, 0.045, 2359), "standard": (267, 0.651, 195)}


def test_held_out_calls_within_35_percent():
    for name, spans, tokens, tier, _, by_role, _ in MEASURED_RUNS:
        estimate, _ = _projection(tier, spans, tokens)
        counted = sum(by_role.values())
        assert abs(estimate["expected_calls"] - counted) <= TOLERANCE * counted, name


def test_held_out_atoms_within_35_percent():
    for name, spans, tokens, tier, atoms, _, _ in MEASURED_RUNS:
        estimate, _ = _projection(tier, spans, tokens)
        assert abs(estimate["n_atoms"] - atoms) <= TOLERANCE * atoms, name


def test_held_out_dollars_within_35_percent(pinned_list_prices):
    for name, spans, tokens, tier, _, _, usd in MEASURED_RUNS:
        _, priced = _projection(tier, spans, tokens)
        assert priced is not None, name
        assert abs(priced - usd) <= TOLERANCE * usd, name


def test_held_out_triage_dollars_within_35_percent(pinned_list_prices):
    # The triage runs are the ones the extractor's output per call is for: a flat profile
    # priced them 21-43% low before the per-tier extractor profile.
    triage = [row for row in MEASURED_RUNS if row[3] == "triage"]
    assert len(triage) == 9
    for name, spans, tokens, tier, _, _, usd in triage:
        _, priced = _projection(tier, spans, tokens)
        assert abs(priced - usd) <= TOLERANCE * usd, name


def test_the_expected_calls_sit_between_the_no_escalation_and_the_full_escalation_bounds():
    for name, spans, tokens, tier, _, _, _ in MEASURED_RUNS:
        estimate, _ = _projection(tier, spans, tokens)
        assert (estimate["total_calls"] <= estimate["expected_calls"]
                <= estimate["total_calls_if_every_pair_escalates"]), name
        assert estimate["expected_calls"] == sum(estimate["by_role"].values()), name


def test_the_atom_count_takes_the_token_rule_when_the_artifact_has_more_tokens_than_spans():
    everything = resolve_settings(None, {"all_atoms": True})
    assert everything.estimate(7)["estimate"]["n_atoms"] == 3          # spans only
    assert everything.estimate(7, n_tokens=198)["estimate"]["n_atoms"] == 8
    # Short spans with many tokens: the span rule was the smaller of the two.
    assert everything.estimate(20, n_tokens=900)["estimate"]["n_atoms"] == 39


def test_the_extractor_profile_follows_the_tier_and_study_takes_the_standard_one():
    assert call_tokens("extractor", 1000, "triage") == (1016, 2359)
    assert call_tokens("extractor", 1000, "standard") == (918, 195)
    assert call_tokens("extractor", 1000, "study") == call_tokens("extractor", 1000, "standard")
    assert call_tokens("extractor", 1000) == call_tokens("extractor", 1000, "standard")


def test_an_unpriced_model_gives_no_dollar_figure_rather_than_zero():
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


def test_the_cli_prices_a_triage_estimate_with_the_triage_extractor_profile(tmp_path, capsys):
    from dokimi.commands.novelty import _role_models

    root = tmp_path / "s"
    root.mkdir()
    body = "\n\n".join(f"Section {i}. Do not enable the legacy driver number {i} on a "
                        "production host without a rollback plan." for i in range(60))
    (root / "SKILL.md").write_text(f"---\nname: s\ndescription: d\n---\n\n# S\n\n{body}\n",
                                   encoding="utf-8")
    args = _parse("novelty", "analyze", "--artifact", f"skill:{root}", "--tier", "triage",
                  "--estimate-only")
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    by_role, tokens = payload["estimate"]["by_role"], payload["tokens"]
    models = _role_models(args)
    triage = estimate_usd(by_role, tokens, models, "triage")
    assert payload["expected_usd"] == triage
    assert triage != estimate_usd(by_role, tokens, models, None)
    # The artifact's token count is an input: a figure at 0 tokens is a different one.
    assert tokens > 100 and triage != estimate_usd(by_role, 0, models, "triage")
    assert "usd_extrapolated" not in payload


def test_list_cost_usd_distinguishes_an_unpriced_model_from_a_free_one(monkeypatch):
    import litellm

    from dokimi.llm_pricing import list_cost_usd

    # cost_per_token prices a model it has no entry for at 0.0 when the route is known.
    monkeypatch.setattr(litellm, "cost_per_token", lambda **_: (0.0, 0.0))
    assert list_cost_usd("not-in-the-price-map-xyz", "openai", 100, 10) is None
    monkeypatch.setitem(litellm.model_cost, "free-in-the-price-map-xyz",
                        {"litellm_provider": "openai", "mode": "chat",
                         "input_cost_per_token": 0.0, "output_cost_per_token": 0.0})
    assert list_cost_usd("free-in-the-price-map-xyz", "openai", 100, 10) == 0.0


def test_estimate_only_stdout_stays_json_for_a_model_litellm_cannot_route(tmp_path, capsys):
    # litellm prints a "Provider List" banner to stdout before it raises for such a model.
    root = tmp_path / "s"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                   encoding="utf-8")
    args = _parse("novelty", "analyze", "--artifact", f"skill:{root}", "--tier", "standard",
                  "--estimate-only", "--model", "foo-bar-9", "--provider", "openai")
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["expected_usd"] is None


def test_estimate_only_names_only_the_unpriced_role_on_stderr(tmp_path, capsys):
    root = tmp_path / "s"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                   encoding="utf-8")
    base = ["novelty", "analyze", "--artifact", f"skill:{root}", "--tier", "triage",
            "--estimate-only"]
    priced = _parse(*base).model
    args = _parse(*base, "--judge-model", "zzz", "--judge-provider", "openai",
                  "--extract-model", priced)
    args.func(args)
    out = capsys.readouterr()
    assert json.loads(out.out)["expected_usd"] is None
    assert "no list price in the litellm price map for judge (zzz)" in out.err
    assert "answerer (" not in out.err
    assert "extractor (" not in out.err


def test_call_tokens_never_goes_negative_for_a_tiny_artifact():
    # The answerer's base is negative (-38 + 0.789 per artifact token), so a 10-token
    # artifact would price a negative prompt without the floor.
    for role in ("extractor", "answerer", "judge"):
        for tier in (None, "triage", "standard"):
            assert min(call_tokens(role, 10, tier)) >= 0
    assert call_tokens("answerer", 10)[0] == 0


def _one_line_skill(root):
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                   encoding="utf-8")
    return root


def test_estimate_only_gives_a_null_dollar_figure_when_no_judge_resolves(tmp_path, capsys):
    # A real run exits here (openrouter has no default judge); the preview still prints.
    args = _parse("novelty", "analyze", "--artifact", f"skill:{_one_line_skill(tmp_path / 's')}",
                  "--tier", "triage", "--estimate-only", "--judge-provider", "openrouter")
    args.func(args)
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["expected_usd"] is None
    assert payload["estimate"]["expected_calls"] > 0
    assert "USD estimate is unavailable: --judge-provider openrouter" in out.err


def test_every_extra_arm_adds_an_answer_and_a_grade_per_probe_to_the_bill(tmp_path, capsys):
    by_role = {}
    for arms in ("no_artifact,full", "no_artifact,full,shuffled"):
        skill = _one_line_skill(tmp_path / str(len(by_role)))
        args = _parse("novelty", "analyze", "--artifact", f"skill:{skill}", "--tier", "standard",
                      "--estimate-only", "--arms", arms)
        args.func(args)
        by_role[arms] = json.loads(capsys.readouterr().out)["estimate"]["by_role"]
    two, three = by_role["no_artifact,full"], by_role["no_artifact,full,shuffled"]
    assert three["answerer"] * 2 == two["answerer"] * 3 > 0
    assert three["judge"] * 2 == two["judge"] * 3 > 0
    assert three["extractor"] == two["extractor"]


def test_a_study_run_counts_its_reversal_probes_in_the_answerer_and_judge_calls():
    study = resolve_settings("study", {})
    by_role = study.estimate(40, n_tokens=2000, n_arms=2)["estimate"]
    assert by_role["reversal_probes"] > 0
    expected = (by_role["paired_probes"] + by_role["reversal_probes"]) * 2
    assert by_role["by_role"]["answerer"] == by_role["by_role"]["judge"] == expected


def test_the_answerer_artifact_term_scales_with_the_arms_that_carry_it():
    two = call_tokens("answerer", 6000)[0]
    assert call_tokens("answerer", 6000, None, 0.5) == call_tokens("answerer", 6000)
    three = call_tokens("answerer", 6000, None, 2 / 3)[0]
    four = call_tokens("answerer", 6000, None, 3 / 4)[0]
    assert two < three < four
    assert three == round(-38 + 0.789 * (2 / 3) / 0.5 * 6000)
    # The judge never sees the artifact, so its input does not move with the arms.
    assert call_tokens("judge", 6000, None, 3 / 4) == call_tokens("judge", 6000)


def test_the_cli_prices_three_context_arms_above_two(tmp_path, capsys):
    root = tmp_path / "s"
    root.mkdir()
    body = "\n\n".join(f"Section {i}. Do not enable legacy driver {i} on a production host."
                        for i in range(60))
    (root / "SKILL.md").write_text(f"---\nname: s\ndescription: d\n---\n\n# S\n\n{body}\n",
                                   encoding="utf-8")
    figures = []
    for arms in ("no_artifact,full", "no_artifact,full,shuffled"):
        args = _parse("novelty", "analyze", "--artifact", f"skill:{root}", "--tier", "triage",
                      "--arms", arms, "--estimate-only")
        args.func(args)
        figures.append(json.loads(capsys.readouterr().out))
    two, three = figures
    # Three arms run 1.5x the answerer and judge calls, and the answerer's calls carry more of
    # the artifact each, so the dollars rise by more than the calls alone.
    calls = (three["estimate"]["by_role"]["answerer"] / two["estimate"]["by_role"]["answerer"])
    assert calls == 1.5
    assert three["expected_usd"] > two["expected_usd"]


def test_an_estimate_for_an_artifact_beyond_the_fitted_range_says_it_extrapolates(
        tmp_path, capsys):
    root = tmp_path / "s"
    root.mkdir()
    body = "\n\n".join(f"Section {i}. " + "Do not enable the legacy driver on a host. " * 12
                        for i in range(120))
    (root / "SKILL.md").write_text(f"---\nname: s\ndescription: d\n---\n\n# S\n\n{body}\n",
                                   encoding="utf-8")
    args = _parse("novelty", "analyze", "--artifact", f"skill:{root}", "--tier", "triage",
                  "--estimate-only")
    args.func(args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["tokens"] > FITTED_TOKENS_MAX
    assert payload["usd_extrapolated"] is True
    assert "extrapolates" in captured.err


def test_the_cli_passes_each_arm_share_to_the_dollar_figure(tmp_path, capsys):
    from dokimi.commands.novelty import _role_models

    root = tmp_path / "s"
    root.mkdir()
    body = "\n\n".join(f"Section {i}. Do not enable legacy driver {i} on a production host."
                        for i in range(60))
    (root / "SKILL.md").write_text(f"---\nname: s\ndescription: d\n---\n\n# S\n\n{body}\n",
                                   encoding="utf-8")
    for arms, share in (("no_artifact,full,shuffled", 2 / 3),
                        ("no_artifact,full,placebo,shuffled", 3 / 4)):
        args = _parse("novelty", "analyze", "--artifact", f"skill:{root}", "--tier", "triage",
                      "--arms", arms, "--estimate-only")
        args.func(args)
        payload = json.loads(capsys.readouterr().out)
        by_role, tokens = payload["estimate"]["by_role"], payload["tokens"]
        models = _role_models(args)
        assert payload["expected_usd"] == estimate_usd(by_role, tokens, models, "triage", share)
        # The 0.5 share a two-arm run uses would price the answerer at the two-arm rate.
        assert payload["expected_usd"] != estimate_usd(by_role, tokens, models, "triage", 0.5)


@pytest.mark.parametrize("tokens, flagged", [(FITTED_TOKENS_MAX, False),
                                             (FITTED_TOKENS_MAX + 1, True)])
def test_the_extrapolation_flag_starts_just_above_the_fitted_range(
        tmp_path, capsys, monkeypatch, tokens, flagged):
    from types import SimpleNamespace

    import dokimi.novelty.spans as spans

    # The CLI reads the token count from the segmenter; a stub with exactly `tokens` tokens
    # puts the artifact on either side of the fitted range.
    monkeypatch.setattr(spans, "segment", lambda text: [SimpleNamespace(tokens=tokens)])
    assert FITTED_TOKENS_MAX == 6723  # the largest measured artifact, as the docs state
    args = _parse("novelty", "analyze", "--artifact", f"skill:{_one_line_skill(tmp_path / 's')}",
                  "--tier", "triage", "--estimate-only")
    args.func(args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["tokens"] == tokens
    assert ("usd_extrapolated" in payload) is flagged
    assert ("extrapolates" in captured.err) is flagged
