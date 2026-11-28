# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The per-call token profile of ``novelty/tiers.py`` against 17 measured paid runs (#1217).

The runs are 17 paid ``skill novelty analyze`` runs of 2026-11-28 at dokimi 26e30838, on seven
skill entry points of 640 to 5,954 tokens: ``us.anthropic.claude-sonnet-5-5`` extracts and
judges, ``us.anthropic.claude-haiku-5-5`` answers, both on amazon-bedrock. Two arms
(``no_artifact``, ``full``), budgets (0.75, 0.5), ``--seed 0``, a fresh response cache. The halves
split by artifact: ``fit`` is four artifacts, ``held`` the other three.

Each run's calls are priced from the profile and compared with the tokens the run billed, so the
check isolates the profile from the call-count model (atoms and escalations) that
``test_novelty_tiers.py`` holds to its October runs.
"""

from __future__ import annotations

import pytest

from dokimi.novelty.tiers import CALL_TOKENS, EXTRACTOR_TOKENS, call_tokens

#: Row: id, half, spans, artifact tokens, tier (``None`` untiered), atoms after the cap,
#: ``{role: (calls, input, output tokens)}``, billed USD. Numbers only; see the module docstring.
PROFILE_RUNS = [
    ("n01", "fit", 31, 640, "triage", 22, {"extractor": (3, 2886, 6463), "answerer": (44, 25628, 11534), "judge": (44, 20769, 3220)}, 0.167717),
    ("n02", "fit", 31, 640, "standard", 23, {"extractor": (109, 83674, 20841), "answerer": (92, 53918, 25934), "judge": (92, 44262, 6857)}, 0.606332),
    ("n03", "fit", 31, 640, "study", 23, {"extractor": (114, 90158, 24193), "answerer": (134, 79100, 38953), "judge": (134, 64482, 11098)}, 0.758534),
    ("n04", "fit", 31, 640, None, 21, {"extractor": (94, 72989, 20856), "answerer": (126, 74366, 37634), "judge": (126, 62004, 10967)}, 0.675917),
    ("n05", "fit", 55, 1612, "triage", 25, {"extractor": (4, 4790, 9575), "answerer": (50, 65149, 19202), "judge": (50, 26019, 5355)}, 0.249737),
    ("n06", "fit", 55, 1612, "standard", 40, {"extractor": (225, 313166, 48206), "answerer": (152, 198190, 60781), "judge": (152, 80646, 17697)}, 1.64655),
    ("n07", "fit", 151, 4234, "triage", 25, {"extractor": (8, 11826, 19983), "answerer": (50, 171908, 17756), "judge": (50, 26807, 3727)}, 0.374478),
    ("n08", "fit", 148, 5954, "triage", 25, {"extractor": (9, 18357, 33348), "answerer": (50, 282127, 20870), "judge": (50, 28115, 5337)}, 0.570286),
    ("n09", "fit", 148, 5954, "standard", 60, {"extractor": (281, 1045950, 73126), "answerer": (230, 1298587, 93442), "judge": (230, 128552, 26291)}, 3.871729),
    ("n10", "fit", 148, 5954, "study", 118, {"extractor": (524, 1884474, 132550), "answerer": (654, 3699857, 267004), "judge": (654, 369235, 76445)}, 7.810941),
    ("n11", "held", 47, 1186, "triage", 25, {"extractor": (3, 4051, 7220), "answerer": (50, 59050, 18651), "judge": (50, 26347, 4060)}, 0.207709),
    ("n12", "held", 41, 1278, "triage", 25, {"extractor": (3, 3758, 6491), "answerer": (50, 52145, 19988), "judge": (50, 27534, 6102)}, 0.224095),
    ("n13", "held", 41, 1278, "standard", 27, {"extractor": (97, 98702, 25449), "answerer": (102, 106595, 40901), "judge": (102, 54245, 11203)}, 0.773876),
    ("n14", "held", 41, 1278, "study", 33, {"extractor": (127, 143033, 29819), "answerer": (180, 189378, 75172), "judge": (180, 97535, 21338)}, 1.154153),
    ("n15", "held", 142, 4003, "triage", 25, {"extractor": (7, 11503, 21802), "answerer": (50, 178685, 21652), "judge": (50, 26935, 5852)}, 0.420322),
    ("n16", "held", 142, 4003, "standard", 60, {"extractor": (262, 763662, 72217), "answerer": (236, 843848, 96559), "judge": (236, 125599, 23277)}, 3.152739),
    ("n17", "held", 142, 4003, "study", 120, {"extractor": (502, 1474460, 136276), "answerer": (676, 2424654, 297963), "judge": (676, 366327, 76681)}, 6.82285),
]

#: The per-token list prices (input, output) of the models of the runs, as ``_PINNED_LIST_PRICES``
#: in ``test_novelty_tiers.py`` has them. Copied, not imported, so this module reads alone.
_SONNET = (2.2e-06, 1.1e-05)
_HAIKU = (1.1e-07, 5.5e-07)
PRICES = {"extractor": _SONNET, "answerer": _HAIKU, "judge": _SONNET}

HELD = [row for row in PROFILE_RUNS if row[1] == "held"]
FIT = [row for row in PROFILE_RUNS if row[1] == "fit"]


def _ols(points):
    """Closed-form least squares ``y = a + b x``: ``(a, b)``."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in points)
             / sum((x - mean_x) ** 2 for x, _ in points))
    return mean_y - slope * mean_x, slope


def _per_call(rows, role, column):
    """One point per run: the artifact's tokens, and the role's tokens per call."""
    return [(row[3], row[6][role][column] / row[6][role][0]) for row in rows]


def _mean(points):
    return sum(y for _, y in points) / len(points)


def _fit_in(rows, role):
    base, per_token = _ols(_per_call(rows, role, 1))
    return round(base), round(per_token, 3)


def _fit_out_flat(rows, role):
    return round(_mean(_per_call(rows, role, 2))), 0.0


def _priced(role, calls, tokens_in, tokens_out):
    price_in, price_out = PRICES[role]
    return calls * (tokens_in * price_in + tokens_out * price_out)


def _projected(row, role):
    _, _, _, tokens, tier, _, roles, _ = row
    return _priced(role, roles[role][0], *call_tokens(role, tokens, tier))


def _measured(row, role):
    """What the run's tokens of ``role`` cost at the pinned prices."""
    _, tokens_in, tokens_out = row[6][role]
    return _priced(role, 1, tokens_in, tokens_out)


def test_the_token_profile_is_the_least_squares_fit_of_the_fit_rows():
    triage = [row for row in FIT if row[4] == "triage"]
    pooled = [row for row in FIT if row[4] != "triage"]
    assert [row[0] for row in triage] == ["n01", "n05", "n07", "n08"]
    assert [row[0] for row in pooled] == ["n02", "n03", "n04", "n06", "n09", "n10"]
    out_base, out_per_token = _ols(_per_call(triage, "extractor", 2))
    assert EXTRACTOR_TOKENS == {
        "triage": (*_fit_in(triage, "extractor"), round(out_base), round(out_per_token, 3)),
        "standard": (*_fit_in(pooled, "extractor"), *_fit_out_flat(pooled, "extractor")),
    }
    assert CALL_TOKENS == {
        role: (*_fit_in(FIT, role), *_fit_out_flat(FIT, role)) for role in ("answerer", "judge")
    }


def test_held_out_runs_priced_from_their_calls_within_15_percent():
    assert len(HELD) == 7
    for row in HELD:
        projected = sum(_projected(row, role) for role in PRICES)
        measured = sum(_measured(row, role) for role in PRICES)
        assert projected == pytest.approx(measured, rel=0.15), row[0]


def test_held_out_roles_priced_from_their_calls_within_20_percent():
    for row in HELD:
        for role in PRICES:
            assert _projected(row, role) == pytest.approx(_measured(row, role), rel=0.20), (
                row[0], role)
