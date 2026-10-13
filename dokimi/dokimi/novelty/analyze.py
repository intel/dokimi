# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Wire the novelty stages into one analysis of one artifact against one model.

Stage order is a dependency order, not a preference: the need graph must exist before
value can be judged (relevance comes from outside the artifact), and the paired probes
must exist before categories can use the measured known/new axis instead of asking a
model what it knew.

Every stage is skippable, because cost is real — the measurement is
``atoms × probes × arms × (answer + grade)`` — and a cheap run that says what it skipped
is more useful than an expensive run nobody starts. Each skip appends a warning to the
artifact so a partial run is never quoted as a full sweep.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from . import atoms as atoms_mod
from . import attribute, bundle as bundle_mod
from . import classify, items as items_mod
from . import measure, needs, probes, report, retention, shorten, spans
from .client import LLMClient, roll_up_cost
from .retention import RetentionOracle


@dataclass
class AnalyzeConfig:
    """What to run and how much of it."""

    artifact_ref: str
    artifact_kind: str = "skill"
    name: str = ""
    description: str = ""
    frontmatter: str = ""
    max_atoms: int | None = 120
    paraphrases: int = 1
    reversals: bool = True
    arms: tuple[str, ...] = (measure.BASELINE_ARM, "full")
    budgets: tuple[float, ...] = (0.75, 0.5)
    concurrency: int = 5
    allow_self_judge: bool = False
    verify_faithfulness: bool = True
    classify_content: bool = True
    shorten_artifact: bool = True
    # Check each atom against the span the extractor cited for it. Cheap (one cached oracle
    # verdict per cited pair, mostly resolved lexically) and it decides whether the span
    # views may assert a colour: 0.19 of atoms in the study were unrecoverable from their
    # cited file, so a run that skips this must not present attribution as confirmed.
    confirm_carriers: bool = True
    placebo_text: str | None = None
    cache_dir: Path | None = None
    seed: int = 0
    # Measured over the study corpus, 0.72 of a skill's words live outside `SKILL.md`, so
    # an entry-point-only run says little about a progressive-disclosure artifact. The
    # flag is recorded in `run_config` because the two modes are not comparable cells.
    include_resources: bool = False
    resource_roles: tuple[str, ...] | None = None


def _prompts_sha256() -> str:
    """Digest of every stage prompt, so a prompt edit is visible as a different measurement."""
    mods = (atoms_mod, classify, measure, needs, probes, retention)
    texts = sorted(v for m in mods for k, v in vars(m).items()
                   if k.endswith(("_PROMPT", "_SYSTEM")) and isinstance(v, str))
    return "sha256:" + hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest()


def _run_config(config: "AnalyzeConfig") -> dict:
    """What decided this cell's numbers beyond the models: two cells compare only if these match."""
    return {
        "seed": config.seed,
        "max_atoms": config.max_atoms,
        "paraphrases": config.paraphrases,
        "reversals": config.reversals,
        "arms": list(config.arms),
        "budgets": list(config.budgets),
        "include_resources": config.include_resources,
        "resource_roles": list(config.resource_roles) if config.resource_roles else None,
        "stages": {
            "verify": config.verify_faithfulness, "classify": config.classify_content,
            "shorten": config.shorten_artifact, "confirm_carriers": config.confirm_carriers,
        },
        "prompts_sha256": _prompts_sha256(),
    }


@dataclass
class AnalyzeResult:
    """Everything one analysis produced, plus the renderable views."""

    artifact: dict
    attributions: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    atoms: list = field(default_factory=list)
    spans: list = field(default_factory=list)
    measure_result: object | None = None


def kind_agreement_warnings(rows: dict[str, dict]) -> list[str]:
    """Warn only where the gate would judge: same floor, same minimum atom count."""
    from ..gate.novelty_gate import DEFAULTS

    out: list[str] = []
    for arm, row in sorted(rows.items()):
        if (
            row["agreement"] is not None
            and row["n_atoms_multi_kind"] >= DEFAULTS["min_atoms_for_kind_agreement"]
            and row["agreement"] < DEFAULTS["min_kind_agreement"]
        ):
            reading = (
                "`redundant` in this arm is wording-matched recall rather than prior "
                "knowledge" if arm == measure.BASELINE_ARM
                else "those atoms' outcomes are a property of the phrasing"
            )
            out.append(
                f"in arm `{arm}`, {len(row['disagreeing'])} of {row['n_atoms_multi_kind']} "
                f"atoms asked by more than one probe kind got different verdicts per kind; "
                f"{reading}"
            )
    return out


def analyze(
    text: str,
    config: AnalyzeConfig,
    *,
    extractor: LLMClient,
    answerer: LLMClient,
    judge: LLMClient,
    ref_digest: str | None = None,
    artifact_bundle: "bundle_mod.Bundle | None" = None,
    tier: dict | None = None,
) -> AnalyzeResult:
    """Run the full pipeline over one artifact's text.

    Args:
        text: The artifact body (frontmatter already split off into ``config``).
        config: What to run.
        extractor: Builds atoms, probe wordings, the need graph, and the value verdicts.
        answerer: The model under measurement.
        judge: Grades responses; must differ from ``answerer`` unless allowed.
        ref_digest: Content digest of the artifact, so a stored result can be tied to the
            exact bytes it was measured on.
        tier: The resolved cost tier and the claims it forfeits, recorded in
            ``run_config`` and appended to the artifact's warnings so a cheap run cannot be
            quoted as a sweep.
        artifact_bundle: The resolved bundle ``text`` was rendered from. Supplying it adds
            per-file provenance: every span, atom and outcome can then be traced to the
            file it came from, which is the cut an author needs to decide what belongs in
            the body. Without it the run is entry-point-only and says so.
    """
    warnings: list[str] = []
    span_list = spans.segment(text)
    extraction = atoms_mod.ExtractionReport()
    atom_list = atoms_mod.extract_atoms(
        span_list, client=extractor, cap=config.max_atoms, report=extraction
    )
    dropped = extraction.warning(len(span_list))
    if dropped:
        warnings.append(dropped)
    if tier:
        # From what this run did, not the tier's text: an override or an atom set under
        # the cap changes which claims are forfeit.
        from .tiers import forfeits

        tier = dict(tier, forfeits=forfeits(
            {name: getattr(config, name) for name in (
                "max_atoms", "paraphrases", "reversals", "verify_faithfulness",
                "classify_content", "shorten_artifact", "confirm_carriers")},
            n_atoms=len(atom_list)))
        for forfeit in tier["forfeits"]:
            warnings.append(f"tier `{tier['name']}` forfeits: {forfeit}")
        if tier.get("overrides"):
            warnings.append(
                f"tier `{tier['name']}` was overridden by flags "
                f"({', '.join(sorted(tier['overrides']))}); the forfeits above follow the "
                "settings this run used"
            )
    else:
        from .tiers import FORFEITS

        # A tier states these through its forfeits; the same flags typed alone did not.
        if not config.paraphrases:
            warnings.extend(FORFEITS["paraphrases"])
        if not config.reversals:
            warnings.extend(FORFEITS["reversals"])
    sampling = (
        f"cap-per-artifact:{config.max_atoms}" if config.max_atoms is not None else "full"
    )
    if config.max_atoms is not None and len(atom_list) >= config.max_atoms:
        warnings.append(
            f"atom extraction hit the cap of {config.max_atoms}; rates describe the "
            "sampled atoms, not the whole artifact"
        )
    if not atom_list:
        warnings.append("no atoms with span provenance were extracted; nothing to measure")

    faithfulness = None
    if not config.verify_faithfulness:
        warnings.append(
            "atoms were not checked against the spans they came from (--no-verify): "
            "`faithfulness` is empty, so distortion by the extractor is unbounded for "
            "this run"
        )
    if config.verify_faithfulness and atom_list:
        faithfulness = atoms_mod.verify_atoms(atom_list, span_list, client=extractor)
        distorted = faithfulness.get("distorted", 0) + faithfulness.get("unsupported", 0)
        if distorted:
            warnings.append(
                f"{distorted} atoms are distorted or unsupported by their own spans; they "
                "stay in the set and are visible in atoms.faithfulness"
            )

    graph = needs.build_need_graph(
        config.name or config.artifact_ref, config.description, client=extractor
    )
    if not graph.nodes:
        warnings.append(
            "the need graph is empty, so relevance fell back to a flat weight; `value` and "
            "out-of-scope detection are not meaningful for this run"
        )
    relevance = needs.match_atoms(atom_list, graph)

    probe_set = probes.build_probe_set(
        atom_list, client=extractor,
        paraphrases=config.paraphrases, reversals=config.reversals,
    )
    probe_list = probe_set.probes
    # A generated wording that lost the version bound, or a "reversal" that never
    # reversed, corrupts the answer key the judge grades against. Rejects are dropped,
    # but a high reject rate also means the kind it hit was measured on a thinner probe
    # budget than the run claims, so it has to be visible.
    for kind, row in probe_set.fidelity.items():
        if row["generated"] and row["rejected"] / row["generated"] > 0.2:
            warnings.append(
                f"{row['rejected']} of {row['generated']} generated {kind} wordings failed "
                f"the fidelity check and were dropped "
                f"({', '.join(sorted(row['reasons']))}); {kind} probes cover "
                f"{row['kept']} of {len(atom_list)} atoms"
            )
    measure_result = measure.run_measurement(
        atom_list, probe_list, text,
        answerer=answerer, judge=judge, arms=config.arms,
        concurrency=config.concurrency, allow_self_judge=config.allow_self_judge,
        placebo_text=config.placebo_text, seed=config.seed,
    )
    if config.allow_self_judge and judge.model == answerer.model:
        warnings.append(
            "judge and answerer are the same model (--allow-self-judge): scores are inflated"
        )
    outcomes = measure.atom_outcomes(measure_result.runs, with_arm=measure_result.with_arm)
    # What *kind* of knowledge the model lacked, not just how much (skill-novelty-evidence.md
    # §10.1). The slices are the actionable cut for an author; the caveats ride along in the report.
    by_knowledge_kind = {
        scoring: measure.outcomes_by_atom_kind(
            measure_result.runs, atom_list,
            with_arm=measure_result.with_arm, scoring=scoring,
        )
        for scoring in ("lenient", "strict")
    }

    categories: dict[str, str] = {}
    conflicts: list[dict] = []
    if config.classify_content and atom_list:
        verdicts = classify.judge_value(
            atom_list, client=extractor, task=graph.task_statement()
        )
        conflicts = classify.detect_conflicts(atom_list, client=extractor)
        classify.apply_conflicts(verdicts, conflicts)
        categories = classify.categorize(atom_list, verdicts, outcomes)
    elif not config.classify_content:
        warnings.append("content categories were not judged (--no-classify)")

    attributions = attribute.attribute_spans(
        span_list, atom_list, outcomes=outcomes, categories=categories
    )
    oracle = RetentionOracle(client=extractor, cache_dir=config.cache_dir)
    confirmation: dict | None = None
    if config.confirm_carriers and atom_list:
        confirmation = attribute.carrier_confirmation(span_list, atom_list, oracle=oracle)
        if confirmation["n_unlocated"]:
            warnings.append(
                f"{confirmation['n_unlocated']} of {confirmation['n_atoms']} atoms were not "
                "recoverable from any span cited for them; they are reported as unlocated "
                "rather than attributed, and are not necessarily wrong (in the study the "
                "faithfulness check called 46 of 49 such atoms supported)"
            )
    elif not config.confirm_carriers:
        warnings.append(
            "span citations were not confirmed (--no-confirm-carriers): the span views "
            "report the extractor's citation as given"
        )
    candidates: list = []
    if config.shorten_artifact and atom_list:
        candidates = shorten.build_candidates(
            span_list, atom_list, oracle=oracle, budgets=config.budgets,
            relevance=relevance, outcomes=outcomes, categories=categories, seed=config.seed,
        )
        for candidate in candidates:
            candidate.text = shorten.render(
                span_list, candidate.kept_span_ids, frontmatter=config.frontmatter
            )
        unsafe = [c for c in candidates if not c.safe]
        if unsafe:
            warnings.append(
                f"{len(unsafe)} shortening candidates drop protected spans; they are reported "
                "so the knowledge-vs-protection gap is visible, not offered as accepted output"
            )

    # Does an atom's own question and its rewording agree? A `redundant` that survives only
    # the artifact's phrasing is not prior knowledge (open question the kind split exists for).
    item_stats = {
        "kind_agreement": {
            arm: items_mod.kind_agreement(measure_result.runs, arm=arm)
            for arm in (measure.BASELINE_ARM, measure_result.with_arm)
        },
        "discrimination": items_mod.discrimination(
            [{"model": answerer.model, "runs": measure_result.runs}],
            arm=measure_result.with_arm,
        ),
    }
    warnings.extend(kind_agreement_warnings(item_stats["kind_agreement"]))

    bundle_summary: dict = {}
    if artifact_bundle is not None:
        bundle_summary = artifact_bundle.summary()
        pointer_ids = bundle_mod.pointer_spans(span_list, artifact_bundle)
        # Pointer spans carry no knowledge by design ("see `references/x.md`"), so they
        # are named rather than scored as waste. 53 of 2,600 spans in the study corpus, plus
        # the per-file headings the bundle renderer injects.
        bundle_summary["pointer_spans"] = {
            "n": len(pointer_ids),
            "ids": sorted(pointer_ids),
            "tokens": sum(s.tokens for s in span_list if s.id in pointer_ids),
        }
        bundle_summary["by_source"] = _outcomes_by_source(
            span_list, atom_list, outcomes, artifact_bundle
        )
        # The measurable share, not the on-disk one: a skill whose resources are all its
        # own evals has nothing a bundle run would add, so pointing at the flag would be
        # advice that cannot be followed.
        measurable = artifact_bundle.measurable_outside_share()
        if not artifact_bundle.include_resources and measurable >= 0.5:
            warnings.append(
                f"{measurable:.0%} of this artifact's words are in bundled files that were "
                "not measured (--include-resources); every rate here describes the entry "
                "point only"
            )
        content_excluded = artifact_bundle.excluded_content()
        if artifact_bundle.include_resources and content_excluded:
            warnings.append(
                f"{len(content_excluded)} bundled files were excluded from the "
                "measured text (see bundle.excluded for each reason); they are counted in "
                "resource_words_on_disk but carry no atoms"
            )

    payload = report.build_novelty_artifact(
        artifact_kind=config.artifact_kind,
        artifact_ref=config.artifact_ref,
        artifact_tokens=sum(s.tokens for s in span_list),
        ref_digest=ref_digest,
        model=answerer.model,
        provider=answerer.provider,
        judge_model=judge.model,
        atoms=atom_list,
        sampling=sampling,
        measure_result=measure_result,
        span_summary=spans.span_summary(span_list, atom_list, atom_cap=config.max_atoms),
        by_knowledge_kind=by_knowledge_kind,
        probe_fidelity=probe_set.as_dict(),
        items=item_stats,
        bundle=bundle_summary,
        attribution_summary=attribute.attribution_summary(
            attributions, confirmation=confirmation
        ),
        categories=classify.category_counts(categories) if categories else {},
        faithfulness=faithfulness,
        graph_summary=graph.summary(),
        relevance=needs.relevance_summary(relevance),
        retention_stats=oracle.stats(),
        candidates=candidates,
        dead_weight=attribute.dead_weight(attributions),
        sole_carriers=attribute.sole_carriers(attributions),
        topics=attribute.topic_rollup(attributions),
        conflicts=conflicts,
        cost=roll_up_cost([extractor, answerer, judge]),
        warnings=warnings,
    )
    payload["run_config"] = _run_config(config)
    payload["run_config"]["tier"] = tier or None
    return AnalyzeResult(
        artifact=payload,
        attributions=attributions,
        candidates=candidates,
        atoms=atom_list,
        spans=span_list,
        measure_result=measure_result,
    )


def _outcomes_by_source(
    span_list: list, atom_list: list, outcomes: dict[str, str], artifact_bundle
) -> list[dict]:
    """The paired 2x2 cut by which bundle file carried the atom.

    This is the authoring decision the bundle view exists for: a reference file whose
    atoms are all `redundant` is a file the model already knows, and a reference file full
    of `added` is knowledge that may belong where the agent will actually read it. An atom
    is attributed to the file of its first cited span; an atom citing spans in two files
    is rare and counted once, at the first.
    """
    source_of_span = {span.id: artifact_bundle.source_of(span.start) for span in span_list}
    rows: dict[str, dict] = {}
    for atom in atom_list:
        source = next(
            (source_of_span[sid] for sid in atom.span_ids if sid in source_of_span), ""
        )
        row = rows.setdefault(source, {"source": source, "n_atoms": 0})
        row["n_atoms"] += 1
        verdict = outcomes.get(atom.id)
        if verdict:
            row[verdict] = row.get(verdict, 0) + 1
    # A measured file that produced no atom is a result, not an absence: it is either
    # pointers and prose, or knowledge the extractor could not formalize.
    for item in artifact_bundle.files:
        rows.setdefault(item.path, {"source": item.path, "n_atoms": 0})
    by_path = {item.path: item for item in artifact_bundle.files}
    for source, row in rows.items():
        item = by_path.get(source)
        row["role"] = item.role if item else "unknown"
        row["words"] = item.words if item else 0
    return sorted(rows.values(), key=lambda r: (-r.get("added", 0), r["source"]))


def load_artifact_text(ref: str) -> tuple[str, str, str, str, str]:
    """Resolve an artifact reference to ``(kind, name, description, frontmatter, body)``.

    Supported today: ``skill:<path>`` (a ``SKILL.md`` or its directory) and
    ``text:<path>`` for any other context artifact. A plain path is treated as text, and
    a text artifact has no description — which means its need graph, and therefore its
    relevance weighting, is weaker. The analyzer says so in a warning rather than
    pretending the two cases are equivalent.
    """
    if ref.startswith("skill:"):
        from dokimi.skills.loader import load_skill

        skill = load_skill(ref.removeprefix("skill:"))
        # Verbatim, as shorten.render promises: a rebuilt `description: a: b` is not YAML.
        lines = skill.path.read_text(encoding="utf-8").lstrip("﻿").splitlines()
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        frontmatter = "\n".join(lines[: end + 1])
        return "skill", skill.name, skill.description, frontmatter, skill.body
    path = Path(ref.removeprefix("text:"))
    if not path.is_file():
        raise FileNotFoundError(f"artifact not found: {path}")
    return "text", path.stem, "", "", path.read_text(encoding="utf-8")
