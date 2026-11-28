# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``skill novelty analyze --dry-run`` splits its dollars by role and names the least sure one (#631)."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.llm_pricing import list_cost_usd
from dokimi.novelty.cost import least_sure_role, role_models, role_usd
from dokimi.novelty.tiers import call_tokens

ROLES = ("extractor", "answerer", "judge")


def _estimate(tmp_path, capsys, *flags):
    body = "\n\n".join(f"Section {i}. Do not enable the legacy driver number {i} on a "
                       "production host without a rollback plan." for i in range(60))
    (tmp_path / "SKILL.md").write_text(f"---\nname: s\ndescription: d\n---\n\n# S\n\n{body}\n",
                                       encoding="utf-8")
    args = build_parser().parse_args(["skill", "novelty", "analyze", "--artifact",
                                      f"skill:{tmp_path}", "--dry-run", *flags])
    args.func(args)
    out = capsys.readouterr()
    return args, json.loads(out.out), out.err


TIERS = [("--tier", "triage"), ("--tier", "standard"), ("--tier", "study"), ()]


@pytest.mark.parametrize("tier_flags", TIERS)
def test_usd_by_role_prices_each_roles_calls_and_sums_to_expected_usd(tmp_path, capsys, tier_flags):
    args, payload, _ = _estimate(tmp_path, capsys, *tier_flags)
    tier = tier_flags[1] if tier_flags else None
    split = payload["usd_by_role"]
    assert list(split) == list(ROLES)
    assert sum(split.values()) == pytest.approx(payload["expected_usd"], abs=1e-5)
    models = role_models(args)
    for role in ROLES:
        model, provider = models[role]
        cost = list_cost_usd(model, provider, *call_tokens(role, payload["tokens"], tier))
        assert split[role] == pytest.approx(payload["estimate"]["by_role"][role] * cost, abs=1e-6)


def _reason(role):
    return (f"the {role} has the most dollars, so an error in its call count or its token "
            "profile moves the figure most")


@pytest.mark.parametrize("tier_flags", TIERS)
def test_every_tier_names_the_role_with_the_largest_dollars(tmp_path, capsys, tier_flags):
    _, payload, _ = _estimate(tmp_path, capsys, *tier_flags)
    split, least = payload["usd_by_role"], payload["least_sure_role"]
    assert split[least["role"]] == max(split.values())
    assert least["reason"] == _reason(least["role"])


def test_some_tier_names_a_role_other_than_the_extractor(tmp_path, capsys):
    named = set()
    for tier_flags in TIERS:
        _, payload, _ = _estimate(tmp_path, capsys, *tier_flags)
        named.add(payload["least_sure_role"]["role"])
    assert named != {"extractor"}


def test_least_sure_role_is_the_largest_dollars_and_a_tie_goes_to_the_first_role():
    judge = {"extractor": 0.2, "answerer": 0.1, "judge": 0.7}
    assert least_sure_role(judge) == {"role": "judge", "reason": _reason("judge")}
    # a tie goes to the first of ROLES
    tied = {"extractor": 0.1, "answerer": 0.4, "judge": 0.4}
    assert least_sure_role(tied)["role"] == "answerer"
    assert least_sure_role({"extractor": 0.3, "answerer": 0.3, "judge": 0.3})["role"] == "extractor"


def test_panel_judges_after_the_first_count_under_judge(tmp_path, capsys):
    judges = ["openai/gpt-5.6-luna", "anthropic/claude-sonnet-5", "openai/gpt-6-luna"]
    flags = [f for judge in judges for f in ("--judge", judge)]
    _, one, _ = _estimate(tmp_path, capsys, "--tier", "standard", "--judge", judges[0])
    _, three, _ = _estimate(tmp_path, capsys, "--tier", "standard", *flags)
    assert three["usd_by_role"]["extractor"] == one["usd_by_role"]["extractor"]
    assert three["usd_by_role"]["answerer"] == one["usd_by_role"]["answerer"]
    assert three["usd_by_role"]["judge"] > one["usd_by_role"]["judge"]
    assert sum(three["usd_by_role"].values()) == pytest.approx(three["expected_usd"], abs=1e-5)


def test_an_unpriced_model_gives_null_role_dollars_and_no_least_sure_role(tmp_path, capsys):
    _, payload, err = _estimate(tmp_path, capsys, "--tier", "standard", "--model", "foo-bar-9",
                                "--provider", "openai")
    assert payload["expected_usd"] is None
    assert payload["usd_by_role"] is None
    assert payload["least_sure_role"] is None
    assert "Least sure of" not in err


def test_role_usd_is_none_for_an_unpriced_role(tmp_path, capsys):
    args, payload, _ = _estimate(tmp_path, capsys, "--tier", "standard", "--model", "foo-bar-9",
                                 "--provider", "openai")
    assert role_usd(args, payload["tokens"], payload["estimate"]["by_role"], "standard") is None


def test_stderr_names_the_role_its_percent_and_the_reason_before_the_estimate_note(
        tmp_path, capsys):
    _, payload, err = _estimate(tmp_path, capsys, "--tier", "study")
    split = payload["usd_by_role"]
    role = payload["least_sure_role"]["role"]
    percent = 100 * split[role] / sum(split.values())
    line = f"Least sure of: {role} ({percent:.0f}% of expected_usd): {_reason(role)}\n"
    assert line in err
    assert err.index(line) < err.index("Estimate only, nothing was measured.")
