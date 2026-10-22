# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Novelty gate — soft checks over one ``novelty.v1`` artifact.

Every check here is **soft**: it reports and never blocks. That is a measured decision, not
caution. Subsampling the study's probes showed the cross-artifact ranking of `added` collapsing
below a 0.75 probe budget (Spearman 0.43-0.63 at 0.10), and a threshold at `added >= 0.15` flipped
its verdict on up to 24% of artifacts at a 0.25 budget. Correlating `added` against the measured
task delta pointed the same way from the other side: ranking 13 artifacts inside one model gave
-0.446, with the per-model correlations disagreeing in sign. So a novelty number is a profile of
one ``(artifact, model)`` pair, a threshold on it is an opinion about that pair's probe budget, and
nothing here may fail a build. ``severity`` is kept in the check because the posture is expected to
harden per-check once probe counts rise, not because anything is blocking today.

Thresholds come from the study; each one names the observation it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dokimi.metrics.bundle_coverage import COVERAGE_BASES, Coverage

# ── Thresholds (override per call) ────────────────────────────────────────────

DEFAULTS: dict[str, Any] = {
    # Lenient `added` floor. The weakest artifact measured in the study sat at 0.131; below that an
    # artifact is mostly tokens. Lenient, not strict, because this is a floor and partial credit is
    # the generous reading — the *ranking* work is strict-only (lenient correlated at 0.126).
    "min_added_lenient": 0.15,
    # Dead-token ceiling. 11 of 13 analyzed skills sat at <=0.17; the two outliers (0.50, 0.74)
    # were a judging-cap artifact and are 0.068 and 0.064 on the full atom set (evidence §10.7).
    "max_frac_carrying_nothing": 0.25,
    # Share of the artifact's tokens extraction must have read for that ceiling to be checked.
    # Below it the fraction is over a remnant (one read span in fifty passed as "measured"),
    # so it is reported, not judged. Conventional, not measured: the same 0.80 as the other
    # share floors here.
    "min_frac_read": 0.80,
    # Share of atoms confirmed against the span cited for them. 0.907 was achievable with the
    # heading path prepended, and 0.19 of atoms were not recoverable from their cited file at all,
    # so this is set below the measured best rather than at it.
    "min_frac_located": 0.80,
    # Share of atom pairs the conflict check must have compared for a count of 0 to clear
    # `harmful_atoms`. 1.0: analyze sends the capped atom set in one call, so a lower share
    # means a call failed or split, and any unread pair can hide a contradiction.
    "min_conflict_coverage": 1.0,
    # Below this, a candidate's `value` is atom coverage per token wearing a value label.
    "min_value_coverage": 0.50,
    # Share of an artifact's words that may sit in unmeasured bundled files before the run
    # stops describing the artifact. Measured: the study corpus keeps 0.72 of its words
    # outside SKILL.md, per skill median 0.43 and worst 0.99, so an entry-point-only run of
    # a progressive-disclosure skill is a run about a fraction of it.
    "max_unmeasured_outside_share": 0.50,
    # Share of multi-kind atoms whose kinds must agree, in the *worse* of the arms the run
    # measured. Below this, those outcomes are wording-dependent, which is the one thing a
    # `redundant` verdict may not be. Conventional, not measured on this data.
    "min_kind_agreement": 0.80,
    # Multi-kind atoms below which agreement is reported but not judged: one disagreeing
    # atom out of one is a 0.0 that says nothing.
    "min_atoms_for_kind_agreement": 5,
    # Share of generated probe wordings dropped by the fidelity check. Above this, the kind
    # that hit it covered materially fewer atoms than `atoms.n` suggests.
    "max_probe_malformed_rate": 0.20,
}

# Every check ships at one of these severities. "fail" is unused on purpose (see module docstring).
SEVERITIES = ("info", "warn", "fail")


@dataclass
class NoveltyCheck:
    """One check against one novelty artifact."""

    name: str
    passed: bool
    value: Any
    threshold: Any
    message: str
    severity: str = "warn"
    measured: bool = True
    #: What ``value`` is a share of, for a check whose figure depends on it
    #: (``artifact_coverage``: the record's ``coverage_basis``); ``None`` otherwise.
    basis: str | None = None

    def as_dict(self) -> dict:
        """Serializable form (what the scorecard carries)."""
        row = {
            "name": self.name,
            "measured": self.measured,
            "passed": self.passed,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "severity": self.severity,
        }
        if self.basis is not None:
            row["basis"] = self.basis
        return row


@dataclass
class NoveltyVerdict:
    """All checks for one ``(artifact, model)`` cell."""

    artifact_ref: str = ""
    model: str = ""
    checks: list[NoveltyCheck] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """Always False today: no novelty check is hard. Kept so callers read the posture."""
        return any(not c.passed for c in self.checks if c.severity == "fail")

    @property
    def warnings(self) -> list[NoveltyCheck]:
        """Checks that did not pass at ``warn`` severity."""
        return [c for c in self.checks if not c.passed and c.severity == "warn"]

    @property
    def notes(self) -> list[NoveltyCheck]:
        """Checks that did not pass at ``info`` severity — observations, not problems."""
        return [c for c in self.checks if not c.passed and c.severity == "info"]

    @property
    def status(self) -> str:
        """``PASS`` / ``WARN`` / ``FAIL``, without a build meaning attached."""
        if self.blocking:
            return "FAIL"
        return "WARN" if self.warnings else "PASS"

    @property
    def not_measured(self) -> list[str]:
        """Checks whose input this run did not produce; they pass without having been run."""
        return [c.name for c in self.checks if not c.measured]

    def as_dict(self) -> dict:
        """Serializable form."""
        return {
            "artifact_ref": self.artifact_ref,
            "model": self.model,
            "status": self.status,
            "blocking": self.blocking,
            "not_measured": self.not_measured,
            "checks": [c.as_dict() for c in self.checks],
        }


def _pair_coverage(raw: Any) -> tuple[int, int] | None:
    """``(compared, total)`` from ``conflict_coverage``, or ``None`` when it is not readable.

    Validated artifacts already carry non-negative integers; the bool, type and
    ``compared > total`` guards are for dicts that never went through the schema, which
    cannot express the cross-field bound.
    """
    if not isinstance(raw, dict):
        return None
    compared, total = raw.get("pairs_compared"), raw.get("pairs_total")
    if (
        isinstance(compared, bool) or isinstance(total, bool)
        or not isinstance(compared, int) or not isinstance(total, int)
        or compared < 0 or total < 0 or compared > total
    ):
        return None
    return compared, total


def evaluate_novelty(artifact: dict, thresholds: dict[str, Any] | None = None) -> NoveltyVerdict:
    """Run the soft checks over one ``novelty.v1`` artifact.

    A check whose input the artifact does not carry passes with ``measured=False``, never as a
    silent pass: a run that skipped a stage must not read as a run that cleared it.
    """
    cfg = {**DEFAULTS, **(thresholds or {})}
    checks: list[NoveltyCheck] = []
    paired = artifact.get("paired", {}) or {}
    spans = artifact.get("spans", {}) or {}

    # ── 1. Is there enough new knowledge to be worth the tokens? ─────────────
    added = (paired.get("lenient") or {}).get("added")
    n_paired = (paired.get("lenient") or {}).get("n_paired", 0)
    if added is None or not n_paired:
        checks.append(NoveltyCheck(
            name="added_floor", passed=True, value=None, threshold=cfg["min_added_lenient"],
            severity="info", measured=False, message="no paired probes: `added` was not measured",
        ))
    else:
        checks.append(NoveltyCheck(
            name="added_floor",
            passed=added >= cfg["min_added_lenient"],
            value=round(added, 4), threshold=cfg["min_added_lenient"],
            message=(
                f"lenient `added` {added:.3f} over {n_paired} paired probes "
                f"(floor {cfg['min_added_lenient']}); this is one (artifact, model) pair and is "
                "not comparable to another artifact's figure"
            ),
        ))

    # ── 2. Does the artifact contradict itself or the model's correct prior? ──
    # The design asks for "no harmful atom on a rare-critical need path". novelty.v1 carries the
    # conflict count and the need-path classes separately, not joined per atom, so this checks the
    # count and says which of the two it is checking rather than implying the stronger one.
    categories = artifact.get("categories") or {}
    harmful = categories.get("harmful_conflicting", 0)
    degraded = (paired.get("strict") or {}).get("degraded")
    # A count of 0 clears the check only when the conflict check read the pair space: enough
    # of the pairs compared, over an atom set that was not capped. A conflict that was found is
    # reported whatever the coverage. A missing `conflict_coverage` is an artifact written
    # before the field existed and keeps the historical reading; one that is present but
    # unreadable is not measured.
    has_coverage = "conflict_coverage" in artifact
    conflict_coverage = _pair_coverage(artifact.get("conflict_coverage"))
    compared, total = conflict_coverage or (0, 0)
    share = compared / total if total else 1.0
    capped = bool(spans.get("capped_atom_set"))
    if not categories:
        message = (
            "not measured: atoms were not classified, so harmful/conflicting content is "
            "unknown"
        )
        measured = False
    elif harmful:
        message = f"{harmful} atoms classified harmful/conflicting"
        measured = True
    elif has_coverage and conflict_coverage is None:
        message = (
            "not measured: the conflict_coverage record is malformed, so a count of 0 is not "
            "a cleared check"
        )
        measured = False
    elif conflict_coverage is not None and share < cfg["min_conflict_coverage"]:
        message = (
            f"not measured: the conflict check compared {compared} of {total} atom "
            f"pairs (floor {cfg['min_conflict_coverage']:.0%} of pairs), so a count of 0 "
            "is not a cleared check"
        )
        measured = False
    elif capped:
        message = (
            f"not measured: the atom set was capped ({spans.get('n_atoms_indexed')} indexed), "
            "so 0 conflicts among the sampled atoms is not a cleared check of the artifact"
        )
        measured = False
    else:
        message = "0 atoms classified harmful/conflicting"
        measured = True
    if measured and compared < total:
        message += f"; {compared} of {total} atom pairs were compared"
    if measured and capped:
        message += "; the atom set was capped, so this is a lower bound"
    if degraded is not None:
        message += f"; strict `degraded` {degraded:.3f}"
    if measured:
        message += (
            ". Correctness gates before volume: one study skill carried 33 conflicts and still "
            "routed at 0.810, so it loads and misinforms"
        )
    checks.append(NoveltyCheck(
        name="harmful_atoms",
        passed=not harmful,
        value=harmful if measured else None, threshold=0,
        severity="warn", measured=measured,
        message=message,
    ))

    # ── 3. Dead weight, only when the atom set behind it was not capped ──────
    frac_dead = spans.get("frac_carrying_nothing")
    pointers = ((artifact.get("bundle") or {}).get("pointer_spans") or {})
    pointer_n, pointer_tokens = pointers.get("n", 0), pointers.get("tokens", 0)
    # Spans no extraction call read are outside the fraction (it is over the read text),
    # so the check says how much it could not see rather than passing them as clean, and
    # does not judge the fraction at all when too little was read.
    unread_n, unread_tokens = spans.get("n_unread") or 0, spans.get("tokens_unread") or 0
    total_tokens = spans.get("tokens") or 0
    frac_read = 1 - unread_tokens / total_tokens if total_tokens else 1.0
    unread_note = (
        f". {unread_n} spans ({unread_tokens} of {spans.get('tokens')} tokens) were never "
        "read by extraction and are outside this figure, so it describes the read text only"
        if unread_n else ""
    )
    if frac_dead is None:
        checks.append(NoveltyCheck(
            name="dead_tokens", passed=True, value=None,
            threshold=cfg["max_frac_carrying_nothing"], severity="info", measured=False,
            message="dead-token share was not computed",
        ))
    elif spans.get("capped_atom_set"):
        checks.append(NoveltyCheck(
            name="dead_tokens", passed=True, value=round(frac_dead, 4),
            threshold=cfg["max_frac_carrying_nothing"], severity="info", measured=False,
            message=(
                f"not checked: the atom set was capped ({spans.get('n_atoms_indexed')} indexed), "
                "and a capped sample does not approximate this figure but inverts it — 0.064 read "
                "as 0.743 on the study's largest artifact"
            ),
        ))
    elif unread_n and frac_read <= 0:
        checks.append(NoveltyCheck(
            name="dead_tokens", passed=True, value=None,
            threshold=cfg["max_frac_carrying_nothing"], severity="info", measured=False,
            message=(
                f"not checked: extraction read none of the artifact's text ({unread_n} spans, "
                f"{unread_tokens} tokens unread)"
            ),
        ))
    elif unread_n and frac_read < cfg["min_frac_read"]:
        checks.append(NoveltyCheck(
            name="dead_tokens", passed=True, value=round(frac_dead, 4),
            threshold=cfg["max_frac_carrying_nothing"], severity="info", measured=False,
            message=(
                f"not checked: extraction read {frac_read:.0%} of the artifact's tokens "
                f"({unread_n} spans, {unread_tokens} of {total_tokens} tokens unread; floor "
                f"{cfg['min_frac_read']:.0%}), so the {frac_dead:.3f} carrying no atom is a "
                "figure about the read remnant, not the artifact"
            ),
        ))
    else:
        checks.append(NoveltyCheck(
            name="dead_tokens",
            passed=frac_dead <= cfg["max_frac_carrying_nothing"],
            value=round(frac_dead, 4), threshold=cfg["max_frac_carrying_nothing"],
            message=(
                f"{frac_dead:.3f} of tokens carry no extracted atom (ceiling "
                f"{cfg['max_frac_carrying_nothing']}); an upper bound on waste, not a measurement "
                "of it"
                + (
                    f". {pointer_n} of those spans ({pointer_tokens} tokens) only name a bundled "
                    "file, which is a loading instruction rather than waste"
                    if pointer_n else ""
                )
                + unread_note
            ),
        ))

    # ── 3b. Was the whole artifact measured, or only its entry point? ───────
    # The record owns its arithmetic: which excluded rows are a gap, what each denominator
    # is, and when no figure can be computed. The record also says which denominator it was
    # written for (`coverage_basis`; none means all bundled words), so a stored artifact is
    # judged as it was when it was written. The gate only judges the result.
    coverage = Coverage.from_record(artifact.get("bundle"))
    known = coverage is not None and coverage.basis in COVERAGE_BASES
    unmeasured = (coverage.unmeasured_share(coverage.basis)
                  if coverage is not None and known else None)
    if coverage is None:
        checks.append(NoveltyCheck(
            name="artifact_coverage", passed=True, value=None,
            threshold=cfg["max_unmeasured_outside_share"], severity="info", measured=False,
            message=(
                "this run does not record which files of the artifact it read, so whether it "
                "measured the whole artifact is unknown"
            ),
        ))
    elif not known:
        checks.append(NoveltyCheck(
            name="artifact_coverage", passed=True, value=None,
            threshold=cfg["max_unmeasured_outside_share"], severity="info", measured=False,
            basis=coverage.basis,
            message=(
                f"this run records its coverage on the {coverage.basis!r} basis, which this "
                f"version does not know ({', '.join(COVERAGE_BASES)}), so the share of the "
                "artifact that was read is not judged"
            ),
        ))
    elif unmeasured is None:
        checks.append(NoveltyCheck(
            name="artifact_coverage", passed=True, value=None,
            threshold=cfg["max_unmeasured_outside_share"], severity="info", measured=False,
            message=(
                f"{coverage.unreadable_files} bundled files could not be decoded, so their "
                "word counts are unknown and the share of this artifact that was read "
                "cannot be computed; fix the encoding, or exclude them explicitly, before "
                "reading any coverage figure"
            ),
        ))
    else:
        # The share of the artifact's words that were NOT read, in either mode. An
        # entry-point run leaves all of them out; a bundle run leaves out whatever the word
        # budgets dropped -- which on a 30k-word bundle is most of it at the default
        # 4,000/20,000 caps. Role decisions (a skill's own `evals/`) are never charged as
        # unread. On the readable basis they are not in the denominator either, so for an
        # entry-point run this is the analyzer's `measurable_outside_share` and the two
        # cannot disagree; a record from before the stamp keeps its all-bundled-words value.
        readable = coverage.basis == "readable"
        if not coverage.include_resources:
            mode = "only the entry point was measured"
        elif readable:
            decided = (f", {coverage.decision_files} not measured by role"
                       if coverage.decision_files else "")
            gap_label = "file" if coverage.gap_files == 1 else "files"
            mode = (f"the bundle was measured ({coverage.n_files} files, "
                    f"{coverage.resource_words_measured} of "
                    f"{coverage.measurable_resource_words} readable bundled words, "
                    f"{coverage.gap_files} {gap_label} excluded by the word budgets{decided})")
        else:
            mode = (f"the bundle was measured ({coverage.n_files} files, "
                    f"{coverage.resource_words_measured} of {coverage.resource_words_on_disk} "
                    f"bundled words, {coverage.named_excluded_files} files excluded)")
        advice = (
            "raise --max-file-words / --max-resource-words, or narrow --resource-roles, "
            "before reading these rates as a property of the artifact"
            if coverage.include_resources
            else "re-run with --include-resources before reading these rates as a "
                 "property of the artifact"
        )
        checks.append(NoveltyCheck(
            name="artifact_coverage",
            passed=unmeasured <= cfg["max_unmeasured_outside_share"],
            value=round(unmeasured, 4), threshold=cfg["max_unmeasured_outside_share"],
            basis=coverage.basis,
            message=(
                f"{mode}: {unmeasured:.0%} of this artifact's "
                f"{'readable ' if readable else ''}words were not read "
                f"(ceiling {cfg['max_unmeasured_outside_share']:.0%}); {advice}. A bundle "
                "run is an upper bound on what the artifact can deliver -- whether the "
                "agent loads a file is the agentic track's question"
            ),
        ))

    # ── 3c. Was the answer key the judge graded against intact? ─────────────
    probe_stats = artifact.get("probes") or {}
    malformed = probe_stats.get("malformed_rate")
    # `0.0` over no generated wordings is not a measurement: a run with the paraphrase and
    # reversal probes off (triage) has an empty `by_kind`, and nothing was checked.
    if not probe_stats or malformed is None or (
            not probe_stats.get("by_kind") and not malformed):
        checks.append(NoveltyCheck(
            name="probe_wording_fidelity", passed=True, value=None,
            threshold=cfg["max_probe_malformed_rate"], severity="info", measured=False,
            message=(
                "no generated probe wordings were checked on this run (direct probes only, or a "
                "run that predates the check)"
            ),
        ))
    else:
        worst = max(
            ((kind, row.get("malformed_rate", 0.0))
             for kind, row in (probe_stats.get("by_kind") or {}).items()),
            key=lambda pair: pair[1], default=("", malformed),
        )
        # The aggregate counts too: a record whose per-kind rows are missing or
        # inconsistent used to pass on `0.0` while the message quoted 90%.
        judged = max(worst[1], malformed)
        checks.append(NoveltyCheck(
            name="probe_wording_fidelity",
            passed=judged <= cfg["max_probe_malformed_rate"],
            value=round(judged, 4), threshold=cfg["max_probe_malformed_rate"],
            message=(
                f"{malformed:.0%} of generated probe wordings were dropped as malformed "
                f"(worst kind: {worst[0] or 'n/a'} at {worst[1]:.0%}); those atoms were probed by "
                "fewer kinds than the run's budget implies. The check is mechanical, so this is "
                "a floor on corruption, not a wording-quality score"
            ),
        ))

    # ── 3d. Do an atom's probe kinds agree, or is the outcome a wording? ────
    agreement_block = ((artifact.get("items") or {}).get("kind_agreement") or {})
    # Both arms matter, and for different reasons: in the artifact arm a disagreement makes
    # `added` wording-dependent, in the baseline arm it makes `redundant` wording-matched
    # recall rather than prior knowledge. The check takes the worse of the two and names it.
    # `novelty items --out` writes the same field keyed by source file rather than by arm;
    # each row carries its own `arm`, so read that and ignore the key. Several files can
    # share an arm (two seeds of one model); the worse row per arm is kept, not the last.
    measured_arms: dict[str, Any] = {}
    for key, row in agreement_block.items():
        if (row or {}).get("agreement") is None:
            continue
        arm = row.get("arm") or key
        # A row too thin to judge must not hide a wide row that fails: rank wide rows first.
        def rank(r: dict[str, Any]) -> tuple[bool, float]:
            return (r.get("n_atoms_multi_kind", 0) < cfg["min_atoms_for_kind_agreement"],
                    r["agreement"])
        if arm not in measured_arms or rank(row) < rank(measured_arms[arm]):
            measured_arms[arm] = row
    if not measured_arms:
        checks.append(NoveltyCheck(
            name="probe_kind_agreement", passed=True, value=None,
            threshold=cfg["min_kind_agreement"], severity="info", measured=False,
            message=(
                "no atom was asked by more than one probe kind, so whether its outcome "
                "survives a rewording is unmeasured"
            ),
        ))
    else:
        worst_arm, worst = min(
            measured_arms.items(),
            key=lambda pair: (
                pair[1].get("n_atoms_multi_kind", 0) < cfg["min_atoms_for_kind_agreement"],
                pair[1]["agreement"],
            ),
        )
        value = worst["agreement"]
        thin = worst.get("n_atoms_multi_kind", 0) < cfg["min_atoms_for_kind_agreement"]
        others = ", ".join(
            f"`{arm}` {row['agreement']:.2f}" for arm, row in sorted(measured_arms.items())
            if arm != worst_arm
        )
        checks.append(NoveltyCheck(
            name="probe_kind_agreement",
            passed=thin or value >= cfg["min_kind_agreement"],
            measured=not thin,
            value=round(value, 4), threshold=cfg["min_kind_agreement"],
            message=(
                f"worst arm `{worst_arm}`: {worst['n_agree']} of "
                f"{worst['n_atoms_multi_kind']} atoms asked by several probe kinds got the "
                f"same verdict from all of them (floor {cfg['min_kind_agreement']})"
                + (f"; other arms: {others}" if others else "")
                + (
                    f"; fewer than {cfg['min_atoms_for_kind_agreement']} multi-kind atoms, "
                    "so this is reported and not judged" if thin else ""
                )
                + ". A disagreeing atom's outcome is wording-dependent: in the artifact arm "
                "that makes `added` a property of the phrasing, and in the baseline arm it "
                "makes `redundant` wording-matched recall rather than prior knowledge"
            ),
        ))

    # ── 4. No offered shortening may drop a protected span ──────────────────
    candidates = artifact.get("shortening") or []
    offered = [c for c in candidates if c.get("safe")]
    leaky = [c for c in offered if (c.get("protection") or 0) < 1.0]
    checks.append(NoveltyCheck(
        name="protection_invariant",
        passed=not leaky,
        value=len(leaky), threshold=0, measured=bool(candidates),
        message=(
            f"{len(leaky)} of {len(offered)} offered candidates drop a protected span. The study's "
            "worst candidate kept 0.916 of knowledge at 0.158 protection, which is why protection "
            "is pinned rather than averaged in"
        ),
    ))

    # ── 5. Are the span citations the views colour actually confirmed? ───────
    confirmation = (artifact.get("attribution") or {}).get("carrier_confirmation")
    if not confirmation:
        checks.append(NoveltyCheck(
            name="span_citations_confirmed", passed=True, value=None,
            threshold=cfg["min_frac_located"], severity="info", measured=False,
            message=(
                "span citations were not confirmed for this run, so the span views report the "
                "extractor's citation as given"
            ),
        ))
    else:
        located = confirmation.get("frac_located", 0.0)
        checks.append(NoveltyCheck(
            name="span_citations_confirmed",
            passed=located >= cfg["min_frac_located"],
            value=round(located, 4), threshold=cfg["min_frac_located"],
            message=(
                f"{confirmation.get('n_located')} of {confirmation.get('n_atoms')} atoms were "
                f"recovered from a span cited for them ({confirmation.get('n_unlocated')} "
                "unlocated). An unlocated atom is not wrong — in the study 46 of 49 were called "
                "supported — but no span may be coloured for it"
            ),
        ))

    # ── 6. Is `value` a measured axis on this run, or coverage in disguise? ──
    proxies = [c for c in candidates if c.get("value_is_coverage_proxy")]
    if not candidates:
        checks.append(NoveltyCheck(
            name="value_is_measured", passed=True, value=None, threshold=cfg["min_value_coverage"],
            severity="info", measured=False, message="no shortening candidates were ranked",
        ))
    else:
        coverages = [c.get("value_coverage") for c in candidates if c.get("value_coverage") is not None]
        lowest = min(coverages) if coverages else None
        checks.append(NoveltyCheck(
            name="value_is_measured",
            passed=not proxies,
            value=None if lowest is None else round(lowest, 4),
            threshold=cfg["min_value_coverage"], severity="warn",
            message=(
                f"{len(proxies)} of {len(candidates)} candidates rank by value with value defined "
                "on under half their atoms, so the ranking is atom coverage per token; probe more "
                "atoms per artifact or read the `knowledge` column instead"
            ),
        ))

    # ── 7. Order independence — expected, and recorded rather than judged ───
    order_independent = (artifact.get("controls") or {}).get("order_independent")
    if order_independent is None:
        checks.append(NoveltyCheck(
            name="order_independence", passed=True, value=None, threshold=False,
            severity="info", measured=False, message="the shuffled control was not run",
        ))
    else:
        checks.append(NoveltyCheck(
            name="order_independence",
            passed=not order_independent,
            value=bool(order_independent), threshold=False, severity="info",
            message=(
                "the shuffled control scored at least as high as the artifact as written. Expected, "
                "not a defect: retested behaviourally on the tasks whose graded requirements name "
                "an ordering, scrambling span order cost +0.022 (CI -0.027 to +0.072). Spend "
                "authoring effort on facts and exact values, not on transitions"
            ),
        ))

    return NoveltyVerdict(
        artifact_ref=(artifact.get("artifact") or {}).get("ref", ""),
        model=artifact.get("model", ""),
        checks=checks,
    )


def format_novelty_block(verdicts: list[NoveltyVerdict]) -> str:
    """Render the ``## Novelty gate`` markdown block for one or more cells."""
    lines = ["## Novelty gate", "", "_All novelty checks are soft: they report and never block._"]
    if not verdicts:
        lines.extend(["", "- none"])
        return "\n".join(lines) + "\n"
    for verdict in verdicts:
        lines.extend([
            "",
            f"### `{verdict.artifact_ref or 'unknown'}` on `{verdict.model or 'unknown'}` "
            f"— {verdict.status}"
            + (f" ({len(verdict.not_measured)} of {len(verdict.checks)} not measured)" if verdict.not_measured else ""),
            "",
            "| Check | Status | Value | Threshold | Notes |",
            "| --- | --- | ---: | ---: | --- |",
        ])
        for check in verdict.checks:
            icon = "not measured" if not check.measured else (
                "ok" if check.passed else ("warn" if check.severity == "warn" else "note"))
            lines.append(
                f"| {check.name} | {icon} | {check.value} | {check.threshold} | {check.message} |"
            )
    return "\n".join(lines) + "\n"
