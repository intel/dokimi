# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Named cost tiers, and the validity each one gives up to be cheap.

Instrumented over three real skills with a counting client, the measurement is
``~4 model calls per paired probe`` -- answering and grading are each half the bill on a
corpus whose atoms the retention oracle resolves locally. They are not the whole bill
everywhere: the oracle escalates the ambiguous band to a model, once per atom for carrier
confirmation and once per atom per shortening candidate, and on a corpus that escalates
heavily that term measured 35% of the total. A full-tier run of a 1,700-word skill came to 1,078
calls; the same skill at a triage budget came to 103. So the cost lever is the **probe
budget**, not the stage switches, and a tier is mostly a statement about atoms and probe
kinds.

The reason tiers live here rather than in a wiki: a cheap run that does not say what it
gave up gets quoted as if it were a sweep. Every tier carries ``forfeits`` -- the claims a
run at that tier may not make -- and those strings are written into ``run_config.tier``
and into the artifact's warnings, so the ceiling travels with the number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: What a run gives up, keyed by the setting that is off ("sampled": an atom cap the
#: artifact reached). The forfeits of a run are derived from its resolved settings and,
#: once known, its atom count -- not fixed per tier -- so `--no-verify` adds the
#: faithfulness forfeit, and `--all-atoms` or an atom set under the cap drops the
#: sampled-rate ones.
FORFEITS: dict[str, list[str]] = {
    "sampled": [
        "`added` is a sampled rate over the atom cap: usable as a sign, not as a figure, "
        "and not comparable with another artifact's; pass --all-atoms for the full sweep",
        "`frac_carrying_nothing` is not interpretable on a capped atom set (0.064 read "
        "as 0.743 on the study's largest artifact)",
    ],
    "verify_faithfulness": [
        "atoms are not checked against the spans they were extracted from, so "
        "`faithfulness` is empty and distortion is unbounded",
    ],
    "shorten_artifact": [
        "no shortening candidates are scored, so nothing here says what could be cut",
    ],
    "paraphrases": [
        "no paraphrase probe, so a `redundant` verdict is unprotected against the model "
        "matching the artifact's own wording",
    ],
    "reversals": ["no reversal probe, so planted-inversion resistance is unmeasured"],
    "confirm_carriers": [
        "atoms are not checked against the spans cited for them, so no span colour is "
        "confirmed",
    ],
    "classify_content": [
        "content categories are not judged, so `harmful_conflicting` is unmeasured -- "
        "the one category that can make an artifact worse than nothing",
    ],
}


def forfeits(settings: dict, *, n_atoms: int | None = None) -> list[str]:
    """The claims a run with these settings may not make.

    Args:
        settings: Resolved ``AnalyzeConfig`` fields; a stage switch that is absent is off.
        n_atoms: Atoms extracted, when known. Before extraction a cap is assumed reached.
    """
    cap = settings.get("max_atoms")
    capped = cap is not None and (n_atoms is None or n_atoms >= cap)
    out = list(FORFEITS["sampled"]) if capped else []
    if capped and settings.get("shorten_artifact"):
        out.append(
            "shortening candidates are scored only against the capped atoms, i.e. the "
            "opening of the artifact: a cut of the uncovered tail looks free"
        )
    for setting, lines in FORFEITS.items():
        if setting != "sampled" and not settings.get(setting):
            out.extend(lines)
    return out


#: Tier -> (settings, forfeited claims at those settings).
TIERS: dict[str, dict] = {
    "triage": {"settings": {
        "max_atoms": 25, "paraphrases": 0, "reversals": False,
        "verify_faithfulness": False, "classify_content": False,
        "shorten_artifact": False, "confirm_carriers": False,
    }},
    "standard": {"settings": {
        "max_atoms": 60, "paraphrases": 1, "reversals": False,
        "verify_faithfulness": True, "classify_content": False,
        "shorten_artifact": True, "confirm_carriers": True,
    }},
    "study": {"settings": {
        "max_atoms": 120, "paraphrases": 1, "reversals": True,
        "verify_faithfulness": True, "classify_content": True,
        "shorten_artifact": True, "confirm_carriers": True,
    }},
}
for _spec in TIERS.values():
    _spec["forfeits"] = forfeits(_spec["settings"])

#: The atom cap when neither a tier nor `--max-atoms` says otherwise. The CLI default is
#: `None` (unset) so an explicit `--max-atoms 120` is distinguishable from silence; without
#: that sentinel a user who typed the old default would have had it silently replaced by
#: the tier's.
DEFAULT_MAX_ATOMS = 120

#: The CLI defaults for every tier-controlled flag. A flag whose parsed value differs from
#: its default was set by the user and wins over the tier; ``tests/test_novelty_tiers.py``
#: asserts this table against the parser, so the two cannot drift apart.
FLAG_DEFAULTS: dict[str, object] = {
    "max_atoms": None,
    # After max_atoms on purpose: as without a tier, --all-atoms beats --max-atoms.
    "all_atoms": False,
    "no_paraphrase": False,
    "no_reversal": False,
    "no_verify": False,
    "no_classify": False,
    "no_shorten": False,
    "no_confirm_carriers": False,
}

#: The settings a tier or a flag decides -- the ``AnalyzeConfig`` fields of the same names.
SETTING_NAMES = ("max_atoms", "paraphrases", "reversals", "verify_faithfulness",
                 "classify_content", "shorten_artifact", "confirm_carriers")

#: The settings when no tier is named and no flag is set. ``AnalyzeConfig`` takes its
#: defaults from here, so the two cannot drift apart.
DEFAULT_SETTINGS: dict[str, Any] = {
    "max_atoms": DEFAULT_MAX_ATOMS, "paraphrases": 1, "reversals": True,
    "verify_faithfulness": True, "classify_content": True, "shorten_artifact": True,
    "confirm_carriers": True,
}

#: Which setting each flag decides, and whether the flag negates it. The only flag ->
#: setting mapping: every command that takes these flags resolves them through
#: :func:`resolve_settings`.
_FLAG_TO_SETTING: dict[str, tuple[str, bool]] = {
    "max_atoms": ("max_atoms", False),
    "all_atoms": ("max_atoms", False),
    "no_paraphrase": ("paraphrases", True),
    "no_reversal": ("reversals", True),
    "no_verify": ("verify_faithfulness", True),
    "no_classify": ("classify_content", True),
    "no_shorten": ("shorten_artifact", True),
    "no_confirm_carriers": ("confirm_carriers", True),
}


@dataclass(frozen=True)
class RunSettings:
    """What one run measures and what it may therefore claim.

    ``values`` holds every :data:`SETTING_NAMES` entry, resolved; ``tier`` and
    ``overrides`` say how they were reached, because a ``--tier triage --max-atoms 90`` run
    is not a stock triage run and must not be read as one. The forfeits and warnings
    follow ``values`` (and, once known, the atom count) -- never the tier's name.
    """

    values: dict[str, Any]
    tier: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, config: Any) -> RunSettings:
        """The settings an ``AnalyzeConfig`` carries."""
        return cls(values={name: getattr(config, name) for name in SETTING_NAMES},
                   tier=getattr(config, "tier", None),
                   overrides=dict(getattr(config, "tier_overrides", None) or {}))

    def config_fields(self) -> dict[str, Any]:
        """The ``AnalyzeConfig`` keyword arguments that carry these settings."""
        return {**self.values, "tier": self.tier, "tier_overrides": dict(self.overrides)}

    def forfeits(self, n_atoms: int | None = None) -> list[str]:
        """The claims a run at these settings may not make (see :func:`forfeits`)."""
        return forfeits(self.values, n_atoms=n_atoms)

    def tier_record(self, n_atoms: int | None = None) -> dict | None:
        """``run_config.tier``: name, forfeits, overrides; ``None`` without a tier."""
        if self.tier is None:
            return None
        return {"name": self.tier, "forfeits": self.forfeits(n_atoms),
                "overrides": dict(self.overrides)}

    def warnings(self, n_atoms: int | None = None) -> list[str]:
        """The warnings a result at these settings carries.

        With a tier: its forfeits, recomputed from what the run did, and the overrides.
        Without one, only the dropped probe kinds: a tier states those through its
        forfeits, and the stage switches warn on their own in the analyzer, flag named.
        """
        if self.tier is None:
            out: list[str] = []
            if not self.values["paraphrases"]:
                out.extend(FORFEITS["paraphrases"])
            if not self.values["reversals"]:
                out.extend(FORFEITS["reversals"])
            return out
        out = [f"tier `{self.tier}` forfeits: {line}" for line in self.forfeits(n_atoms)]
        if self.overrides:
            out.append(
                f"tier `{self.tier}` was overridden by flags "
                f"({', '.join(sorted(self.overrides))}); the forfeits above follow the "
                "settings this run used"
            )
        return out

    def estimate(self, n_spans: int, *, n_tokens: int | None = None,
                 budgets: tuple[float, ...] = (), n_arms: int = 2) -> dict:
        """The ``--estimate-only`` bill for an artifact of ``n_spans`` spans.

        The atom count is the larger of one atom per two spans and one per
        :data:`TOKENS_PER_ATOM` tokens (spans alone undercount: short spans, such as table
        rows, yield an atom each), capped at ``max_atoms``. Without ``n_tokens`` only the
        span rule applies. The forfeits assume the cap is reached, since the atom count is
        a guess until extraction runs: an artifact with fewer atoms than the cap drops the
        sampled-rate forfeits.
        """
        cap = self.values["max_atoms"]
        expected = max(1, n_spans // 2)
        if n_tokens is not None:
            expected = max(expected, n_tokens // TOKENS_PER_ATOM)
        n_atoms = expected if cap is None else min(cap, expected)
        return {
            "spans": n_spans,
            "tokens": n_tokens,
            "atom_cap": cap,
            # The full resolved settings, so the projection is the model `estimate_calls`
            # implements rather than a subset of it.
            "estimate": estimate_calls(n_atoms, dict(self.values, budgets=tuple(budgets)),
                                       n_arms=n_arms),
            "tier": self.tier_record(),
            "caveat": (
                "forfeits assume the atom cap is reached; the run recomputes them from the "
                "atoms it extracts" if cap is not None else None
            ),
        }


def resolve_settings(tier: str | None, flags: dict) -> RunSettings:
    """Merge a tier (or the defaults) with the flags the user actually set.

    An explicit flag always wins over the tier -- a tier is a starting point, not a lock --
    and the override is recorded so the result cannot be read as a stock tier run.

    Args:
        tier: Tier name, or ``None`` for :data:`DEFAULT_SETTINGS`.
        flags: Parsed flag values keyed as in :data:`FLAG_DEFAULTS`; a missing flag is
            unset (a command that does not offer it).

    Raises:
        KeyError: Unknown tier name.
    """
    values = dict(TIERS[tier]["settings"]) if tier is not None else dict(DEFAULT_SETTINGS)
    overrides: dict[str, object] = {}
    # Explicit order, not caller insertion order: `--all-atoms` beats `--max-atoms` exactly
    # as it does without a tier, and that precedence must not depend on how the caller
    # happened to build the dict.
    ordered = sorted(flags.items(), key=lambda pair: list(FLAG_DEFAULTS).index(pair[0])
                     if pair[0] in FLAG_DEFAULTS else len(FLAG_DEFAULTS))
    for flag, value in ordered:
        if flag not in _FLAG_TO_SETTING or value == FLAG_DEFAULTS[flag]:
            continue
        if flag == "max_atoms" and flags.get("all_atoms"):
            continue  # discarded by --all-atoms, so not a setting this run applied
        name, negates = _FLAG_TO_SETTING[flag]
        resolved: object = (not value) if negates else value
        if name == "paraphrases":
            resolved = 0 if value else 1
        if flag == "all_atoms":
            resolved = None  # no cap: the full sweep the `study` forfeit points to
        if tier is not None and values.get(name) != resolved:
            overrides[flag] = value
        values[name] = resolved
    return RunSettings(values=values, tier=tier, overrides=overrides)


#: Atoms per artifact token for the atom count when the artifact's size is known. Measured
#: on five standard runs (atoms per token: 1/17 to 1/40, median 1/25); the span rule alone
#: gave 7 atoms for a 198-token skill that yielded 8, and 11 for one that yielded 18.
TOKENS_PER_ATOM = 25

#: The share of oracle pairs (carrier confirmation and shortening candidates) that the
#: oracle escalated to a model. Measured on five standard runs: 0.32-0.70, median 0.51.
ESCALATION_SHARE = 0.5

#: Per call, by role: (input tokens besides the artifact, input tokens per artifact token,
#: output tokens). Measured on the same five runs: the extractor and the answerer send the
#: artifact with every call, the judge sends the answer and the question only.
CALL_TOKENS: dict[str, tuple[int, int, int]] = {
    "extractor": (130, 1, 190),
    "answerer": (20, 1, 240),
    "judge": (460, 0, 85),
}


def call_tokens(role: str, n_tokens: int) -> tuple[int, int]:
    """Input and output tokens of one call of ``role`` on an artifact of ``n_tokens``.

    The extractor's output is a flat figure: on a triage run its extraction calls write
    about 1,700 tokens each, which this does not model, so the dollar figure runs low there.
    """
    base, per_token, out = CALL_TOKENS[role]
    return base + per_token * n_tokens, out


def estimate_usd(by_role: dict[str, int], n_tokens: int,
                 models: dict[str, tuple[str, str]]) -> float | None:
    """The expected dollar cost of ``by_role`` calls at list prices.

    Prices come from litellm's price map (:func:`dokimi.llm.list_cost_usd`), the same map a
    run's own ``cost_usd`` uses, so the figure is the run's price applied to a token profile.
    ``None`` when any role's model has no price: an unpriced call is unknown, not free.

    Args:
        by_role: Calls per role, as :func:`estimate_calls` reports them.
        n_tokens: Tokens in the artifact.
        models: ``(model, provider)`` per role.
    """
    from dokimi.llm import list_cost_usd

    total = 0.0
    for role, calls in by_role.items():
        model, provider = models[role]
        tokens_in, tokens_out = call_tokens(role, n_tokens)
        cost = list_cost_usd(model, provider, tokens_in, tokens_out)
        if cost is None:
            return None
        total += calls * cost
    return round(total, 6)


def estimate_calls(n_atoms: int, settings: dict, *, n_arms: int = 2) -> dict:
    """Project the model-call bill before spending it.

    From the instrumented runs: every probe, reversal included, costs an answer and a grade
    in each arm, and that product is the whole bill to within a few percent. The per-batch
    stages (extraction, wording generation, the need graph, classification) and the
    per-atom faithfulness check add tens of calls, not thousands. Validated against a
    counting client over three real skills at all three tiers: the projection came within
    1-3% of the counted total at every point (see the PR that introduced tiers).

    The oracle's pairs are the other term. Five measured standard runs escalated 36-73% of
    them (median 51%) and the extractor's count equals the stage calls plus those
    escalations, so ``expected_calls`` counts :data:`ESCALATION_SHARE` of the oracle pairs.

    Args:
        n_atoms: Atoms expected after the cap.
        settings: Resolved tier settings (``paraphrases``, ``reversals``, stage switches).
        n_arms: Arms in the run, including the baseline.

    Returns:
        ``{"n_atoms", "paired_probes", "reversal_probes", "probe_calls", "stage_calls",
        "oracle_pairs", "total_calls", "total_calls_if_every_pair_escalates",
        "by_role", "expected_calls"}``.
        ``total_calls`` assumes the retention oracle resolves every pair locally, which is
        what the validated 1-3% figure was measured on; the second total is the other end
        of the range. ``expected_calls`` is the point figure between them, with ``by_role``
        splitting it into the extractor (stages and oracle escalations), answerer and judge.
        The real figure also moves with batch sizes and retries.
    """
    paired = n_atoms * (1 + (1 if settings.get("paraphrases") else 0))
    reversals = n_atoms if settings.get("reversals") else 0
    budgets = len(settings.get("budgets") or ()) or 2
    strategies = settings.get("shortening_strategies", 4)
    # Reversals are graded in every arm too: the baseline arm's resistance to a planted
    # inversion is a reported control, not a by-product.
    probe_calls = (paired + reversals) * n_arms * 2
    stage_calls = (
        max(1, round(n_atoms / 12))                                # extraction batches
        + (n_atoms if settings.get("verify_faithfulness") else 0)   # one per atom
        + (1 if settings.get("paraphrases") else 0)
        + (1 if settings.get("reversals") else 0)
        + 1                                                        # the need graph
        + (2 if settings.get("classify_content") else 0)
    )
    # The retention oracle resolves most pairs locally (retrieval then NLI) and escalates
    # only the ambiguous band, so its cost is a range, not a number: zero when nothing
    # escalates, one call per checked pair when everything does. Both ends are reported
    # because a corpus that escalates heavily measured 1.4-1.6x the no-escalation total.
    oracle_pairs = (
        (n_atoms if settings.get("confirm_carriers") else 0)
        + (n_atoms * budgets * strategies if settings.get("shorten_artifact") else 0)
    )
    escalated = round(ESCALATION_SHARE * oracle_pairs)
    # Answer and grade are each one call per probe and arm; the extractor takes the stages
    # and the escalations, which is how the measured runs split their calls.
    by_role = {
        "extractor": stage_calls + escalated,
        "answerer": (paired + reversals) * n_arms,
        "judge": (paired + reversals) * n_arms,
    }
    return {
        "n_atoms": n_atoms,
        "paired_probes": paired,
        "reversal_probes": reversals,
        "probe_calls": probe_calls,
        "stage_calls": stage_calls,
        "oracle_pairs": oracle_pairs,
        "total_calls": probe_calls + stage_calls,
        "total_calls_if_every_pair_escalates": probe_calls + stage_calls + oracle_pairs,
        "by_role": by_role,
        "expected_calls": sum(by_role.values()),
    }
