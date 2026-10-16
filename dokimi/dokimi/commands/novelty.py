# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""novelty subcommand group: what does this artifact add to this model?

Standalone by design. `novelty analyze` needs an artifact and a model — no question set,
no task suite, no benchmark run — because the first user is an author editing a skill,
not a CI job. The pieces (`atoms`, `probe`, `shorten`, `view`) exist so an expensive
stage can be paid for once and reused.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import sys
from pathlib import Path
from typing import Any

from dokimi.defaults import (
    BEDROCK_MODEL_IDS,
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)

PROVIDERS = ["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"]


def cmd_novelty_analyze(args: argparse.Namespace) -> None:
    """Run the full pipeline over one artifact and write the novelty artifact + views."""
    from dokimi.artifacts import save_artifact
    # Import the symbols, not the module: the package re-exports `analyze` as the
    # function, which shadows the submodule of the same name.
    from dokimi.novelty.analyze import AnalyzeConfig, analyze
    from dokimi.novelty.atoms import ExtractionError
    from dokimi.novelty.measure import SelfJudgeError
    from dokimi.novelty.report import (
        render_annotated_html,
        render_annotated_markdown,
        render_novelty_markdown,
    )

    artifact_bundle = _bundle_from_args(args)
    kind, name, description, frontmatter = (
        artifact_bundle.kind, artifact_bundle.name, artifact_bundle.description,
        artifact_bundle.frontmatter,
    )
    body = artifact_bundle.text

    from dokimi.novelty.spans import segment
    from dokimi.novelty.tiers import FLAG_DEFAULTS, estimate_calls, resolve

    resolved = resolve(args.tier, {flag: getattr(args, flag) for flag in FLAG_DEFAULTS})
    tier_settings = resolved["settings"]
    if resolved["tier"]:
        forfeits = resolved["tier"]["forfeits"]
        overrides = resolved["tier"]["overrides"]
        print(f"Tier `{args.tier}`: " + ", ".join(
            f"{k}={v}" for k, v in sorted(tier_settings.items())), file=sys.stderr)
        for line in forfeits:
            print(f"  forfeits: {line}", file=sys.stderr)
        if overrides:
            print(f"  overridden by flags: {sorted(overrides)}", file=sys.stderr)

    if args.estimate_only:
        spans = segment(artifact_bundle.text)
        cap = tier_settings.get("max_atoms", None if args.all_atoms else _atom_cap(args))
        # One atom per two spans is the density the study's extractor produced.
        expected = max(1, len(spans) // 2)
        n_atoms = expected if cap is None else min(cap, expected)
        # The full resolved settings, so the projection is the model `estimate_calls`
        # implements rather than a subset of it.
        estimate = estimate_calls(n_atoms, {
            "paraphrases": tier_settings.get(
                "paraphrases", 0 if args.no_paraphrase else 1),
            "reversals": tier_settings.get("reversals", not args.no_reversal),
            "verify_faithfulness": tier_settings.get(
                "verify_faithfulness", not args.no_verify),
            "classify_content": tier_settings.get("classify_content", not args.no_classify),
            "shorten_artifact": tier_settings.get("shorten_artifact", not args.no_shorten),
            "confirm_carriers": tier_settings.get(
                "confirm_carriers", not args.no_confirm_carriers),
            "budgets": tuple(args.budgets),
        }, n_arms=len(_arms(args.arms)))
        print(json.dumps({
            "spans": len(spans), "atom_cap": cap, "estimate": estimate,
            "tier": resolved["tier"] or None,
            # The forfeits follow the resolved settings, but the atom count is a guess here:
            # an artifact with fewer atoms than the cap drops the sampled-rate forfeits.
            "caveat": (
                "forfeits assume the atom cap is reached; the run recomputes them from the "
                "atoms it extracts" if cap is not None else None
            ),
        }, indent=2))
        print("Estimate only, nothing was measured. ~4 calls per paired probe is the "
              "measured shape; batch sizes and retries move the real figure.",
              file=sys.stderr)
        if args.max_usd is None:
            print("The estimate counts calls, not dollars: what a call costs depends on the "
                  "artifact's length and the models' prices. Pass --max-usd to stop the run "
                  "at a dollar figure.", file=sys.stderr)
        return

    extractor, answerer, judge = _clients(args)
    # A setting the tier fixes comes from the tier; the rest from the flags.
    flag_settings: dict[str, Any] = {
        "max_atoms": None if args.all_atoms else _atom_cap(args),
        "paraphrases": 0 if args.no_paraphrase else 1,
        "reversals": not args.no_reversal,
        "verify_faithfulness": not args.no_verify,
        "classify_content": not args.no_classify,
        "shorten_artifact": not args.no_shorten,
        "confirm_carriers": not args.no_confirm_carriers,
    }
    config = AnalyzeConfig(
        artifact_ref=args.artifact,
        artifact_kind=kind,
        name=name,
        description=description,
        frontmatter=frontmatter,
        arms=tuple(_arms(args.arms)),
        budgets=tuple(args.budgets),
        concurrency=args.concurrency,
        allow_self_judge=args.allow_self_judge,
        **{k: v for k, v in flag_settings.items() if k not in tier_settings},
        placebo_text=(
            Path(args.placebo_text).read_text(encoding="utf-8")
            if args.placebo_text else None
        ),
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        seed=args.seed,
        include_resources=args.include_resources,
        resource_roles=tuple(args.resource_roles) if args.resource_roles else None,
        **tier_settings,
    )
    try:
        result = analyze(
            body, config,
            extractor=extractor, answerer=answerer, judge=judge,
            ref_digest=_digest(args.artifact, bundle=artifact_bundle),
            artifact_bundle=artifact_bundle,
            tier=resolved["tier"] or None,
        )
    except (SelfJudgeError, ExtractionError) as exc:
        raise SystemExit(str(exc)) from None

    out_dir = Path(args.out_dir) if args.out_dir else _default_dir(args.artifact)
    out_json = Path(args.out_json) if args.out_json else out_dir / "novelty.json"
    save_artifact("novelty", result.artifact, out_json)
    out_md = out_json.with_suffix(".md")
    out_md.write_text(render_novelty_markdown(result.artifact), encoding="utf-8")
    title = f"{name or args.artifact} × {answerer.model}"
    view_path = out_dir / ("annotated.html" if args.view_format == "html" else "annotated.md")
    render = render_annotated_html if args.view_format == "html" else render_annotated_markdown
    view_path.parent.mkdir(parents=True, exist_ok=True)
    view_path.write_text(render(result.attributions, title=title), encoding="utf-8")

    print(f"OK Saved novelty artifact: {out_json}")
    print(f"OK Saved novelty report:   {out_md}")
    print(f"OK Saved annotated view:   {view_path}")
    _print_headline(result.artifact)


def cmd_novelty_atoms(args: argparse.Namespace) -> None:
    """Extract atoms and spans only — the expensive stage, cached for reuse."""
    from dokimi.novelty.atoms import (
        ExtractionError,
        ExtractionReport,
        extract_atoms,
        save_atoms,
        verify_atoms,
    )
    from dokimi.novelty.spans import segment, span_summary

    artifact_bundle = _bundle_from_args(args)
    body = artifact_bundle.text
    extractor = _client(args.extract_model or args.model, args.provider, "extractor", args)
    spans = segment(body)
    cap = None if args.all_atoms else _atom_cap(args)
    extraction = ExtractionReport()
    try:
        atoms = extract_atoms(spans, client=extractor, cap=cap, report=extraction)
    except ExtractionError as exc:
        raise SystemExit(str(exc)) from None
    for warning in extraction.warnings(len(spans)):
        print(f"WARNING {warning}", file=sys.stderr)
    if not args.no_verify:
        verify_atoms(atoms, spans, client=extractor)
    out = Path(args.out) if args.out else _default_dir(args.artifact) / "atoms.json"
    save_atoms(atoms, spans, out, extractor=extractor.model, cap=cap,
               bundle=dict(artifact_bundle.summary(),
                           artifact_name=artifact_bundle.name,
                           artifact_ref=args.artifact,
                           measured_digest=artifact_bundle.measured_digest()),
               extraction=extraction)
    print(f"OK Saved {len(atoms)} atoms over {len(spans)} spans: {out}")
    print(json.dumps(span_summary(spans, atoms, atom_cap=cap), indent=2))


def cmd_novelty_probe(args: argparse.Namespace) -> None:
    """Run the paired probes against a saved atom set and print the 2×2."""
    from dokimi.novelty.atoms import load_atoms
    from dokimi.novelty.measure import SelfJudgeError, run_measurement
    from dokimi.novelty.probes import build_probe_set

    atoms, saved_spans = load_atoms(args.atoms)
    _warn_unread(saved_spans, "the atoms probed here leave out whatever those spans assert")
    saved = json.loads(Path(args.atoms).read_text(encoding="utf-8"))
    body, probed = _saved_atoms_bundle(args, saved)
    extractor, answerer, judge = _clients(args)
    if saved.get("extractor") == answerer.model:
        print(f"WARNING: {answerer.model} extracted these atoms, so the model under measurement "
              "wrote its own answer key; extract with a different --extract-model",
              file=sys.stderr)
    probe_set = build_probe_set(
        atoms, client=None if args.no_paraphrase and args.no_reversal else extractor,
        paraphrases=0 if args.no_paraphrase else 1, reversals=not args.no_reversal,
    )
    probes = probe_set.probes
    for kind, row in sorted(probe_set.fidelity.items()):
        if row["rejected"]:
            print(f"WARNING: dropped {row['rejected']} of {row['generated']} generated {kind} "
                  f"wordings that failed the fidelity check "
                  f"({', '.join(f'{r}={n}' for r, n in sorted(row['reasons'].items()))})",
                  file=sys.stderr)
    try:
        result = run_measurement(
            atoms, probes, body, answerer=answerer, judge=judge,
            arms=tuple(_arms(args.arms)), concurrency=args.concurrency,
            allow_self_judge=args.allow_self_judge, seed=args.seed,
            placebo_text=(
                Path(args.placebo_text).read_text(encoding="utf-8") if args.placebo_text else None
            ),
        )
    except (SelfJudgeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None

    payload = {
        "artifact": args.artifact,
        "model": answerer.model,
        "judge_model": judge.model,
        # Which files this cell measured, so a probe result is as readable as an `analyze`
        # one -- the coverage check needs this block. Both it and the digest describe the
        # text this run injected, not the text the atoms were extracted from: the
        # re-measure step probes an edited SKILL.md with the original atoms, and a digest
        # copied from extraction would make the two runs look like the same text.
        "bundle": {} if probed is None else {
            k: v for k, v in dict(probed.summary(), artifact_name=probed.name,
                                  measured_digest=probed.measured_digest()).items()
            if k != "files"},
        "ref_digest": None if probed is None else probed.measured_digest(),
        "atoms_ref_digest": (saved.get("bundle") or {}).get("measured_digest"),
        "probes": probe_set.as_dict(),
        "paired": {scoring: result.paired(scoring) for scoring in ("lenient", "strict")},
        "arms": {scoring: result.arm_means(scoring) for scoring in ("lenient", "strict")},
        "abstentions": result.abstentions(),
        "reversal_resisted": result.reversal_resisted(),
        "confidence_correlation": result.confidence_correlation(),
        "runs": [row.as_dict() for row in result.runs],
    }
    out = Path(args.out) if args.out else _default_dir(args.artifact) / "probes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"OK Saved {len(result.runs)} probe runs: {out}")
    for scoring in ("lenient", "strict"):
        row = payload["paired"][scoring]
        print(
            f"{scoring:8s} added {row['added']}  already-knew {row['redundant']}  "
            f"degraded {row['degraded']}  still-missing {row['still_missing']}  "
            f"(n={row['n_paired']})"
        )


def cmd_novelty_items(args: argparse.Namespace) -> None:
    """Item analysis across saved probe runs: which atoms separate the models."""
    from dokimi.novelty.items import discrimination, kind_agreement
    from dokimi.novelty.measure import ProbeRun

    cells = []
    for raw in args.probes:
        data = json.loads(Path(raw).read_text(encoding="utf-8"))
        try:
            runs = [
                ProbeRun(
                    atom_id=row["atom_id"], probe_kind=row["probe_kind"], arm=row["arm"],
                    question=row.get("question", ""), response=row.get("response", ""),
                    verdict=row["verdict"], confidence=row.get("confidence"),
                )
                for row in data.get("runs", [])
            ]
        except (KeyError, TypeError) as exc:
            raise SystemExit(
                f"{raw} has a probe run missing {exc}; it is not `novelty probe` output"
            ) from None
        if not runs:
            raise SystemExit(f"{raw} carries no probe runs; item analysis needs `novelty "
                             "probe` output, not a novelty.json")
        cells.append({"model": data.get("model", raw), "runs": runs, "source": raw,
                      "artifact": data.get("artifact"), "ref_digest": data.get("ref_digest")})

    # Correlating atoms across models only means something over one answer key and one
    # probed text. Atom ids are content hashes, so ids collide across artifacts only for
    # identical statements -- but then the "same atom" was asked with different context.
    # A file that does not record a field (older output) is not held against the rest.
    for field in ("artifact", "ref_digest"):
        values = {cell[field] for cell in cells if cell[field] is not None}
        if len(values) > 1:
            raise SystemExit(
                f"these probe runs measured different texts ({field}: "
                f"{', '.join(sorted(values))}); item analysis correlates one artifact's "
                "atoms across models, so pass runs of one artifact at one revision"
            )

    atom_sets = {frozenset(run.atom_id for run in cell["runs"]) for cell in cells}
    if len(atom_sets) > 1:
        print("WARNING: these runs do not cover the same atom set; an atom missing from a "
              "cell is dropped from its own statistic rather than counted as a failure",
              file=sys.stderr)

    payload: dict[str, Any] = {
        "cells": [{"model": cell["model"], "source": cell["source"],
                   "n_runs": len(cell["runs"])} for cell in cells],
        "discrimination": {
            scoring: discrimination(cells, scoring=scoring, arm=args.arm)
            for scoring in ("lenient", "strict")
        },
        # Keyed by source file: two runs of the same model (two seeds, say) must not
        # overwrite each other.
        "kind_agreement": {
            cell["source"]: dict(kind_agreement(cell["runs"], arm=args.arm),
                                 model=cell["model"])
            for cell in cells
        },
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"OK Saved item analysis: {args.out}")

    for scoring in ("lenient", "strict"):
        row = payload["discrimination"][scoring]
        if not row["measured"]:
            print(f"{scoring:8s} discrimination not measured: {row['reason']}")
            continue
        headline = (
            f"{row['n_non_discriminating']} below r={row['min_discrimination']}"
            if row.get("floor_applies")
            else f"floor not applied at {row['n_respondents']} respondents"
        )
        print(f"{scoring:8s} {row['n_atoms']} atoms over {row['n_respondents']} models: "
              f"{headline}, {row['n_negative']} negative, {row['n_undefined']} undefined"
              + (f", {row['n_atoms_dropped']} atoms dropped (not scored by every cell)"
                 if row.get("n_atoms_dropped") else ""))
        scored = [r for r in row["per_atom"] if r["r"] is not None]
        shown = [r for r in scored if r["r"] < 0] or scored[:5]
        if shown:
            label = "negative" if shown and shown[0]["r"] < 0 else f"lowest {len(shown)}"
            print(f"         {label} of {len(scored)} scored:")
            for item in shown[:5]:
                print(f"         {item['atom_id']}  r={item['r']:+.3f}  "
                      f"passed by {item['n_pass']}/{item['n']}")
    for source, agreement in payload["kind_agreement"].items():
        label = f"{agreement['model']} ({Path(source).name})"
        if agreement["agreement"] is None:
            print(f"{label}: kind agreement not measurable (one probe kind per atom)")
        else:
            print(f"{label}: kind agreement {agreement['agreement']:.3f} over "
                  f"{agreement['n_atoms_multi_kind']} atoms asked by several kinds")


def cmd_novelty_routing(args: argparse.Namespace) -> None:
    """Can this artifact be selected against its competitors? Lexical floor, model on top."""
    from dokimi.novelty.analyze import load_artifact_text
    from dokimi.novelty.routing import (
        Candidate,
        headings_as_queries,
        lexical_routing,
        model_routing,
    )

    def candidate(ref: str) -> tuple[Candidate, str]:
        kind, name, description, _frontmatter, body = load_artifact_text(ref)
        if kind == "skill" and not description:
            print(f"WARNING: {ref} has no description; its card is a name, which is all a "
                  "tool schema would show", file=sys.stderr)
        return Candidate(name=name or ref, description=description), body

    try:
        gold, body = candidate(args.artifact)
        competitors = [candidate(ref)[0] for ref in args.competitors or []]
        if args.catalog:
            competitors += _catalog_competitors(args.catalog, gold.name,
                                                {c.name for c in competitors})
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"cannot load an artifact: {exc}") from None
    if not competitors:
        raise SystemExit(
            "routing needs competitors (--competitors, or --catalog for a skill's siblings): "
            "selecting from one candidate measures whether the model will use a tool at all, "
            "not whether this artifact wins"
        )

    # The artifact as its own competitor is a measurement that cannot succeed: the tie
    # break puts the gold card last, so it scores 0 against itself. Checked before the
    # query file is read, so the message is about the real problem.
    if any(c.name == gold.name for c in competitors):
        raise SystemExit(
            f"`{gold.name}` appears in --competitors as well as --artifact; selection "
            "against itself is not a measurement. Drop it from the competitor list."
        )
    # A repeated competitor is counted twice in "vs N competitors" and splits the lexical
    # tie-break between two copies of one card, so the rates describe a different field.
    names = [c.name for c in competitors]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise SystemExit(f"--competitors names {', '.join(f'`{n}`' for n in repeated)} more "
                         "than once; pass each competitor once")

    self_derived = False
    if args.queries:
        raw = Path(args.queries).read_text(encoding="utf-8-sig")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, (list, dict)):      # one-per-line, e.g. "[urgent] build fails"
            data = raw.splitlines()
        listed = data if isinstance(data, list) else data.get("queries", [])
        # A string here is iterable, so `{"queries": "a b"}` once became one query per
        # character and a run over 0 scored queries exited 0.
        if not isinstance(listed, list) or not all(isinstance(q, str) for q in listed):
            raise SystemExit(f"{args.queries}: queries must be a list of strings, "
                             "or one query per line")
        queries = [q for q in listed if q.strip()]
    elif args.queries_from_headings:
        queries = headings_as_queries(body)
        self_derived = True
        print("WARNING: queries derived from the artifact's own headings. The artifact then "
              "defines both the problem scope and its own relevance, so this is triage, not "
              "a routing rate", file=sys.stderr)
    else:
        raise SystemExit(
            "pass --queries <file> with requests written independently of the artifact, or "
            "--queries-from-headings for a self-derived triage run"
        )
    if not queries:
        raise SystemExit("no usable queries")

    candidates = [gold, *competitors]
    lexical = dict(lexical_routing(candidates, queries, gold=0),
                   queries_self_derived=self_derived)
    payload = {
        "artifact": args.artifact,
        "n_candidates": len(candidates),
        "queries_self_derived": self_derived,
        "lexical": lexical,
    }
    if args.model:
        selector = _client(args.model, args.provider, "selector", args)
        payload["model"] = dict(
            model_routing(candidates, queries, client=selector, gold=0, seed=args.seed),
            queries_self_derived=self_derived,
        )

    out = Path(args.out) if args.out else _default_dir(args.artifact) / "routing.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"OK Saved routing measurement: {out}")

    lexical = payload["lexical"]
    print(f"lexical  R@1 {lexical['recall_at_1']}  R@3 {lexical['recall_at_3']}  "
          f"MRR {lexical['mrr']}  over {lexical['n_queries_scored']} of "
          f"{lexical['n_queries_given']} queries vs {len(competitors)} competitors"
          + ("  (R@3 needs more than 3 candidates to mean anything)"
             if lexical["recall_at_3"] is None else ""))
    if "model" in payload:
        row = payload["model"]
        print(f"model    selection {row['selection_rate']}  "
              f"({row['selection_rate_among_answered']} among answered; "
              f"{row['n_abstained']} abstained, {row['n_failed']} failed)  [{row['model']}]")
        print("The model figure is an upper bound on harness behaviour: a harness that "
              "truncates descriptions in its own listing selects on less text.")
    if self_derived:
        print("Self-derived queries: read as triage only.")


def _catalog_competitors(root: str, gold: str, named: set[str]) -> list:
    """Every skill under ``root`` except the one under test and those already named.

    The field a harness would list next to the artifact, rather than one the caller picks.
    """
    from dokimi.novelty.routing import Candidate
    from dokimi.skills import discover_skills

    if not Path(root).is_dir():
        raise ValueError(f"--catalog is not a directory: {root}")
    skills = [s for s in discover_skills(root) if s.name != gold and s.name not in named]
    no_description = [s.name for s in skills if not s.description]
    if no_description:
        print(f"WARNING: {len(no_description)} catalog skill(s) have no description, so their "
              f"card is a name only: {', '.join(sorted(no_description))}", file=sys.stderr)
    print(f"--catalog {root}: {len(skills)} competitors", file=sys.stderr)
    return [Candidate(name=s.name, description=s.description) for s in skills]


def cmd_novelty_shorten(args: argparse.Namespace) -> None:
    """Score shortening candidates on the five axes and optionally write the best one."""
    from dokimi.novelty.analyze import load_artifact_text
    from dokimi.novelty.atoms import load_atoms
    from dokimi.novelty.retention import RetentionOracle, error_warning
    from dokimi.novelty.shorten import build_candidates, pick_best

    atoms, spans = load_atoms(args.atoms)
    _kind, _name, _description, frontmatter, _body = load_artifact_text(args.artifact)
    spans, atoms = _entry_point_only(args.atoms, spans, atoms)
    _warn_unread(spans, "every candidate keeps them, since what they carry is unknown")
    client = _client(args.extract_model or args.model, args.provider, "oracle", args) \
        if not args.lexical_only else None
    oracle = RetentionOracle(
        client=client, cache_dir=Path(args.cache_dir) if args.cache_dir else None
    )
    candidates = build_candidates(
        spans, atoms, oracle=oracle, budgets=tuple(args.budgets), seed=args.seed
    )
    oracle_warning = error_warning(oracle)
    if oracle_warning:
        print(f"WARNING {oracle_warning}", file=sys.stderr)
    print(
        f"{'strategy':20s} {'budget':>7s} {'tok':>6s} {'know':>6s} "
        f"{'value':>6s} {'prot':>6s} safe"
    )
    for candidate in candidates:
        row = candidate.as_dict()
        print(
            f"{row['strategy']:20s} {row['budget_frac']:7.2f} {row['token_frac']:6.2f} "
            f"{row['knowledge']:6.3f} {row['value']:6.3f} {row['protection']:6.3f} {row['safe']}"
        )
    if args.lexical_only:
        print(
            "NOTE lexical-only oracle: contradictions are invisible to it (0.00 measured "
            "contradiction recall), so `knowledge` here is a floor, not a verdict."
        )
    best = pick_best(candidates, require_safe=not args.allow_unsafe)
    if best is None:
        print("No candidate kept every protected span; the artifact's length is its content.")
        return
    print(
        f"Best safe candidate: {best.strategy} @ {best.budget_frac} → {best.token_frac} tokens, "
        f"knowledge {best.knowledge}, protection {best.protection}"
        + (f" (knowledge under-counted: {oracle.errors} adjudications got no usable verdict)"
           if oracle.errors else "")
    )
    if args.out:
        from dokimi.novelty.shorten import render

        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            render(spans, best.kept_span_ids, frontmatter=frontmatter), encoding="utf-8"
        )
        print(f"OK Wrote candidate: {args.out}")


def cmd_novelty_view(args: argparse.Namespace) -> None:
    """Re-render the annotated view from a saved novelty artifact plus its atoms."""
    from dokimi.artifacts import load_artifact
    from dokimi.novelty.atoms import load_atoms
    from dokimi.novelty.attribute import attribute_spans
    from dokimi.novelty.report import render_annotated_html, render_annotated_markdown

    artifact = load_artifact("novelty", Path(args.novelty))
    atoms, spans = load_atoms(args.atoms)
    _warn_unread(spans, "they render as `unread`, not as text that carries nothing")
    # Re-rendering needs per-atom outcomes; the artifact carries aggregates, so a view
    # rebuilt from it alone would colour by category only. Say so instead of implying the
    # colours mean more than they do.
    attributions = attribute_spans(spans, atoms)
    render = render_annotated_html if args.format == "html" else render_annotated_markdown
    title = f"{artifact['artifact']['ref']} × {artifact['model']}"
    out = Path(args.out) if args.out else Path(f"annotated.{args.format}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(attributions, title=title), encoding="utf-8")
    print(f"OK Saved annotated view: {out}")
    print(
        "NOTE colours here come from spans and categories only. For outcome colouring "
        "(added / degraded), render during `novelty analyze`, which has the paired runs."
    )


def _warn_unread(spans: list, consequence: str) -> None:
    """Say on stderr that ``atoms.json`` records spans extraction never read.

    The record is the only trace of them after ``novelty atoms``, so each stage that
    reads the file repeats it, with what it means for that stage.
    """
    unread = [span for span in spans if span.unread]
    if unread:
        print(f"WARNING: {len(unread)} of {len(spans)} spans "
              f"({sum(span.tokens for span in unread)} tokens) were never read by "
              f"extraction (see `extraction` in the atoms file); {consequence}",
              file=sys.stderr)


def _bundle_from_args(args: argparse.Namespace):
    """Resolve `--artifact` to a bundle, with the note that keeps a cheap run honest."""
    from dokimi.novelty.bundle import load_bundle

    try:
        artifact_bundle = load_bundle(
            args.artifact,
            include_resources=args.include_resources,
            roles=tuple(args.resource_roles) if args.resource_roles else None,
            max_file_words=args.max_file_words,
            max_total_words=args.max_resource_words,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"cannot load --artifact {args.artifact}: {exc}") from None
    skipped = artifact_bundle.excluded_content()
    if args.include_resources and skipped:
        print(f"NOTE: {len(skipped)} bundled files will not be measured "
              f"({artifact_bundle.unmeasured_words()} words over the budgets or filtered "
              "by role); see bundle.excluded in the result for each reason",
              file=sys.stderr)
    elif not args.include_resources and artifact_bundle.unmeasured_words():
        # Measurable words only: a skill's own evals are not measured in either mode, so
        # counting them here would promise a rerun that reads nothing more.
        print(f"NOTE: {artifact_bundle.measurable_outside_share():.0%} of this artifact's "
              f"words are in {len(skipped)} bundled files that "
              "will not be measured; pass --include-resources to measure them",
              file=sys.stderr)
    return artifact_bundle


def _artifact_text_for_saved_atoms(args: argparse.Namespace, saved: dict) -> str:
    """The text :func:`_saved_atoms_bundle` rebuilds, for callers that need only that."""
    return _saved_atoms_bundle(args, saved)[0]


def _saved_atoms_bundle(args: argparse.Namespace, saved: dict):
    """Rebuild the exact text the saved atoms were extracted from, and the bundle it is.

    Returns ``(text, bundle)``; the bundle is None for an atoms.json that predates bundle
    provenance.

    `novelty probe` injects this as the artifact arm. If the atoms came from a bundle and
    the probe injected only `SKILL.md`, every atom extracted from a reference file would
    score `still_missing` because the context never contained it -- a measurement error
    that looks like a result. So the bundle mode is taken from `atoms.json`, not from
    flags, and a mismatch is refused rather than reconciled.
    """
    from dokimi.novelty.bundle import load_bundle

    recorded = saved.get("bundle") or {}
    if not recorded:
        print("WARNING: this atoms.json predates bundle provenance; assuming the entry point "
              "only. Re-run `novelty atoms` if the atoms came from bundled files.",
              file=sys.stderr)
        from dokimi.novelty.analyze import load_artifact_text
        return load_artifact_text(args.artifact)[4], None

    recorded_files = recorded.get("files", [])
    if not recorded_files or any("role" not in row or "path" not in row
                                 for row in recorded_files):
        raise SystemExit(
            f"{args.atoms} records a bundle without usable file rows; re-run "
            "`novelty atoms` rather than probing against an answer key whose provenance "
            "cannot be checked"
        )
    # The roles the extraction asked for, not the roles of the files it happened to select:
    # a role whose files were all over budget selects nothing, and a rebuild narrowed to the
    # selected roles would call those files policy and lose the gap.
    requested = recorded.get("resource_roles")
    if requested is None:       # an atoms.json from before the roles were recorded
        requested = sorted({row["role"] for row in recorded_files[1:]}) or None
    # Prefer the budgets the extraction actually used. Older files do not record them, and
    # for those the selection is reproduced from its result: capping at the largest
    # selected file and at the selected total makes the same greedy pick.
    if recorded.get("max_file_words") and recorded.get("max_total_words"):
        budgets = {"max_file_words": int(recorded["max_file_words"]),
                   "max_total_words": int(recorded["max_total_words"])}
    elif recorded.get("include_resources") and all("words" in r for r in recorded_files[1:]):
        selected = [int(row["words"]) for row in recorded_files[1:]]
        budgets = {"max_file_words": max(selected, default=0),
                   "max_total_words": sum(selected)}
    else:
        budgets = {}
    artifact_bundle = load_bundle(
        args.artifact,
        include_resources=bool(recorded.get("include_resources")),
        roles=tuple(requested) if requested is not None
        and recorded.get("include_resources") else None,
        **budgets,
    )
    # An answer key is for one artifact. Without this, skill A's atoms were accepted
    # against skill B whenever the relative paths lined up -- unconditionally so for
    # entry-point-only recordings, where both sides are just ["SKILL.md"].
    recorded_name = recorded.get("artifact_name")
    if recorded_name and recorded_name != artifact_bundle.name:
        raise SystemExit(
            f"these atoms were extracted from `{recorded_name}` and --artifact is "
            f"`{artifact_bundle.name}`: an answer key belongs to one artifact. Extract "
            "atoms for this one instead."
        )
    measured = [row["path"] for row in recorded_files]
    rebuilt = [item.path for item in artifact_bundle.files]
    # A bundled file with the same path but different bytes was edited: still a stale
    # answer key, and a word count cannot see it (`64` to `128` keeps the count). The
    # digest does; the word count stays as the fallback for an atoms.json written before
    # digests were recorded. The entry point is exempt on purpose -- re-measuring a
    # shortened SKILL.md against the original atoms is the authoring path's last step.
    edited = [
        row["path"]
        for row, item in zip(recorded_files[1:], artifact_bundle.files[1:], strict=False)
        if (row.get("sha256") and item.sha256 and row["sha256"] != item.sha256)
        or (not row.get("sha256") and "words" in row and row["words"] != item.words)
    ]
    # The rebuild is filtered to the roles the extraction measured, so a file added since
    # in a role that had no file then (a top-level notes.md, a first scripts/run.sh) is
    # filtered out of `rebuilt` and would pass unnoticed. Compare every path the walk saw
    # against every path the extraction saw instead. Images and other non-text files are
    # never content, so a new one is not a change to the answer key.
    seen = set(measured) | {row.get("path") for row in recorded.get("excluded") or []}
    # A new file in a role nobody asked for (an `evals/` case, by default) cannot change the
    # measured text, and re-extracting would measure it no differently.
    asked = recorded.get("resource_roles")
    added = sorted(
        {row["path"] for row in artifact_bundle.excluded
         if row["path"] not in seen and row["reason"] != "not text"
         and not (asked is not None and row.get("kind") == "policy"
                  and row["role"] not in asked)}
        | (set(rebuilt) - set(measured))
    ) if recorded.get("include_resources") and "excluded" in recorded else []
    if rebuilt != measured or edited or added:
        raise SystemExit(
            "the artifact's files changed since these atoms were extracted "
            f"(atoms.json: {measured}; now: {rebuilt}; edited: {edited}; "
            f"added: {added}). Re-run "
            "`novelty atoms`; probing a "
            "different file set against this answer key would mis-score every moved atom."
        )
    # The entry point is allowed to differ -- that is the re-measure step -- but it is not
    # allowed to differ silently: atoms extracted from text that is gone will read as
    # `still_missing`, which is a result about the edit and must be recognisable as one.
    entry_recorded = recorded_files[0].get("sha256")
    if entry_recorded and entry_recorded != artifact_bundle.files[0].sha256:
        print(f"NOTE: {artifact_bundle.files[0].path} has changed since these atoms were "
              "extracted. That is the re-measure step, so the run proceeds; atoms whose "
              "text was removed will score `still_missing` because of the edit, not because "
              "of the model.", file=sys.stderr)
    if recorded.get("include_resources"):
        print(f"Measuring the bundle recorded in {args.atoms}: {len(measured)} files, "
              f"{recorded.get('resource_words_measured', 0)} bundled words", file=sys.stderr)
    return artifact_bundle.text, artifact_bundle


def _entry_point_only(atoms_path: str, spans: list, atoms: list) -> tuple[list, list]:
    """Restrict shortening to the entry point when the atoms came from a bundle.

    Shortening rewrites one file. A candidate built over bundle spans would propose
    dropping sentences that live in `references/`, and `--out` would render them into the
    shortened `SKILL.md`. So bundle spans are dropped here, loudly: shortening a bundle is
    a different question than shortening a file, and it is not the one this answers.
    """
    saved = json.loads(Path(atoms_path).read_text(encoding="utf-8"))
    recorded = saved.get("bundle") or {}
    if not recorded.get("include_resources"):
        return spans, atoms
    if not recorded.get("files"):
        raise SystemExit(
            f"{atoms_path} records a bundle run without file rows; re-run `novelty atoms`"
        )
    entry = recorded["files"][0]
    if "end" not in entry:
        raise SystemExit(f"{atoms_path} records no span range for {entry.get('path')}")
    kept = [span for span in spans if span.start < entry["end"]]
    kept_ids = {span.id for span in kept}
    # Every cited span must be inside the entry point. An atom with evidence in two files
    # cannot be scored against a candidate that can hold only half of it.
    kept_atoms = [atom for atom in atoms
                  if atom.span_ids and all(sid in kept_ids for sid in atom.span_ids)]
    print(
        f"NOTE these atoms were extracted from a {recorded['n_files']}-file bundle. Shortening "
        f"rewrites one file, so only the {len(kept)} spans of {entry['path']} and the "
        f"{len(kept_atoms)} atoms they carry are scored ({len(spans) - len(kept)} bundle spans "
        "and their atoms dropped); knowledge scores here say nothing about the bundled files.",
        file=sys.stderr,
    )
    return kept, kept_atoms


def register(sub, positive_int) -> None:
    """Add the `novelty` subcommand group."""
    from dokimi.novelty.tiers import TIERS

    novelty_p = sub.add_parser(
        "novelty",
        help="Measure what an artifact's content adds to a model",
    )
    novelty_sub = novelty_p.add_subparsers(dest="novelty_cmd", required=True)

    analyze_p = novelty_sub.add_parser(
        "analyze", help="Full pipeline: atoms → probes → categories → attribution → shortening"
    )
    _add_common(analyze_p, positive_int)
    _add_model_args(analyze_p, positive_int)
    analyze_p.add_argument("--out-dir", default=None, dest="out_dir")
    analyze_p.add_argument("--out-json", default=None, dest="out_json")
    analyze_p.add_argument("--view-format", default="html", choices=["html", "md"],
                           dest="view_format")
    analyze_p.add_argument("--no-classify", action="store_true", dest="no_classify",
                           help="Skip the judged content categories (cheaper; the artifact "
                                "records the skip as a warning)")
    analyze_p.add_argument("--no-shorten", action="store_true", dest="no_shorten")
    analyze_p.add_argument("--no-confirm-carriers", action="store_true",
                           dest="no_confirm_carriers",
                           help="Skip checking each atom against the span cited for it; the "
                                "span views then report the extractor's citation as given "
                                "(0.19 of atoms in the study were not recoverable from it)")
    analyze_p.add_argument("--no-verify", action="store_true", dest="no_verify",
                           help="Skip the per-atom faithfulness check")
    analyze_p.add_argument("--tier", default=None, choices=sorted(TIERS),
                           help="Named cost tier. The measurement is probe-budget bound "
                                "(~4 model calls per paired probe), so a tier is mostly a "
                                "statement about atoms and probe kinds: triage ~100 calls, "
                                "study ~1000 on the same 1,700-word skill. Every tier records "
                                "the claims it forfeits into run_config and the artifact's "
                                "warnings. An explicit flag always wins over the tier")
    analyze_p.add_argument("--estimate-only", action="store_true", dest="estimate_only",
                           help="Print the projected model-call bill and exit without "
                                "spending anything")
    _add_bundle_args(analyze_p, positive_int)
    analyze_p.set_defaults(func=_stops_at_cap(cmd_novelty_analyze))

    atoms_p = novelty_sub.add_parser("atoms", help="Extract knowledge atoms and spans only")
    _add_common(atoms_p, positive_int)
    # The extractor writes the answer key; `analyze` gives that role to the judge, so the
    # two paths default to the same model and a saved atom set matches an analyze run.
    atoms_p.add_argument("--model", default=DEFAULT_JUDGE_MODEL,
                         help="Extractor model when --extract-model is not given "
                              f"(default: {DEFAULT_JUDGE_MODEL}, the default judge)")
    atoms_p.add_argument("--extract-model", default=None, dest="extract_model")
    atoms_p.add_argument("--provider", default=DEFAULT_JUDGE_PROVIDER, choices=PROVIDERS)
    atoms_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens")
    atoms_p.add_argument("--no-verify", action="store_true", dest="no_verify")
    atoms_p.add_argument("--out", default=None)
    _add_spend_cap(atoms_p)
    _add_bundle_args(atoms_p, positive_int)
    atoms_p.set_defaults(func=_stops_at_cap(cmd_novelty_atoms))

    probe_p = novelty_sub.add_parser("probe", help="Run paired probes against a saved atom set")
    _add_common(probe_p, positive_int)
    _add_model_args(probe_p, positive_int)
    probe_p.add_argument("--atoms", required=True, help="atoms.json from `novelty atoms`")
    probe_p.add_argument("--out", default=None)
    probe_p.set_defaults(func=_stops_at_cap(cmd_novelty_probe))

    shorten_p = novelty_sub.add_parser(
        "shorten", help="Score shortening candidates on tokens/knowledge/value/protection/routing"
    )
    _add_common(shorten_p, positive_int)
    shorten_p.add_argument("--atoms", required=True, help="atoms.json from `novelty atoms`")
    # Same model as the retention oracle inside `analyze` (its extractor, i.e. the judge):
    # adjudications are cached per model, and a different one would score other verdicts.
    shorten_p.add_argument("--model", default=DEFAULT_JUDGE_MODEL,
                           help="Retention adjudicator model "
                                f"(default: {DEFAULT_JUDGE_MODEL}, the default judge)")
    shorten_p.add_argument("--extract-model", default=None, dest="extract_model")
    shorten_p.add_argument("--provider", default=DEFAULT_JUDGE_PROVIDER, choices=PROVIDERS)
    shorten_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens")
    shorten_p.add_argument("--lexical-only", action="store_true", dest="lexical_only",
                           help="Run the retention oracle without an LLM adjudicator. Cheap and "
                                "reproducible, but blind to contradictions")
    shorten_p.add_argument("--allow-unsafe", action="store_true", dest="allow_unsafe",
                           help="Allow a best-candidate pick that drops protected spans")
    shorten_p.add_argument("--out", default=None, help="Write the chosen candidate here")
    _add_spend_cap(shorten_p)
    shorten_p.set_defaults(func=_stops_at_cap(cmd_novelty_shorten))

    routing_p = novelty_sub.add_parser(
        "routing", help="Can the artifact be selected against competitors? (name+description)"
    )
    routing_p.add_argument("--artifact", required=True,
                           help="skill:<path> or text:<path> under test")
    routing_p.add_argument("--competitors", nargs="+", default=None,
                           help="Other artifacts the selector sees. Selection among one "
                                "candidate is not routing: the sibling confusion is the "
                                "failure mode (every `dpnp-*` skill scored 0.0 lexically)")
    routing_p.add_argument("--catalog", default=None,
                           help="Directory of skills to take the competitors from: every "
                                "SKILL.md under it except the artifact itself, i.e. the "
                                "siblings a harness lists next to it. Combines with "
                                "--competitors")
    routing_p.add_argument("--queries", default=None,
                           help="JSON list (or one per line) of user-shaped requests that "
                                "should select this artifact, written independently of it")
    routing_p.add_argument("--queries-from-headings", action="store_true",
                           dest="queries_from_headings",
                           help="Derive queries from the artifact's own headings. Triage "
                                "only: the artifact then defines both the problem scope and "
                                "its own relevance, and the result is stamped as such")
    routing_p.add_argument("--model", default=None,
                           help="Also ask this model to pick from the same cards. Optional: "
                                "without it only the free lexical floor is computed")
    routing_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=PROVIDERS)
    routing_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens")
    routing_p.add_argument("--seed", type=int, default=0,
                           help="Seeds the per-query card permutation, so a position-biased "
                                "model scores ~1/N and the run is reproducible")
    routing_p.add_argument("--out", default=None)
    _add_spend_cap(routing_p)
    routing_p.set_defaults(func=_stops_at_cap(cmd_novelty_routing))

    items_p = novelty_sub.add_parser(
        "items", help="Item analysis across saved probe runs: which atoms separate the models"
    )
    items_p.add_argument("--probes", nargs="+", required=True,
                         help="probes.json files from `novelty probe`, one per model. "
                              "Point-biserial discrimination needs at least three, since the "
                              "models are the respondents")
    items_p.add_argument("--arm", default="full",
                         help="Arm to score discrimination and check probe-kind agreement in "
                              "(default: the artifact arm)")
    items_p.add_argument("--out", default=None)
    items_p.set_defaults(func=cmd_novelty_items)

    view_p = novelty_sub.add_parser("view", help="Re-render the annotated artifact view")
    view_p.add_argument("--novelty", required=True, help="novelty.json from `novelty analyze`")
    view_p.add_argument("--atoms", required=True, help="atoms.json from `novelty atoms`")
    view_p.add_argument("--format", default="html", choices=["html", "md"])
    view_p.add_argument("--out", default=None)
    view_p.set_defaults(func=cmd_novelty_view)


def _add_bundle_args(parser: argparse.ArgumentParser, positive_int) -> None:
    """Flags that decide *which files of the artifact* are measured.

    On `atoms` as well as `analyze`: the cheap path is `novelty atoms` once and
    `novelty probe --atoms` per model, so the bundle decision has to be makeable at
    extraction time or the reuse path can only ever see the entry point.
    """
    parser.add_argument("--include-resources", action="store_true", dest="include_resources",
                        help="Measure the whole skill bundle, not just SKILL.md. In the "
                             "33-skill study corpus 0.72 of all words live in bundled "
                             "references/scripts/patterns (worst skill: 0.99), so an "
                             "entry-point-only run says little about a progressive-disclosure "
                             "skill. Costs proportionally more: the bill is ~4 LLM calls per "
                             "paired probe")
    parser.add_argument("--resource-roles", nargs="+", default=None, dest="resource_roles",
                        choices=["reference", "pattern", "eval", "script", "template",
                                 "trigger", "data", "top", "other"],
                        help="Restrict bundled files to these roles (default: every "
                             "role except eval)")
    parser.add_argument("--max-file-words", type=positive_int, default=4000,
                        dest="max_file_words",
                        help="Skip a bundled file larger than this instead of truncating it; a "
                             "cut reference would be attributed as if complete")
    parser.add_argument("--max-resource-words", type=positive_int, default=20000,
                        dest="max_resource_words",
                        help="Total budget across bundled files, taken in role order "
                             "(reference, pattern, eval, rest) and recorded per exclusion")


def _add_common(parser: argparse.ArgumentParser, positive_int) -> None:
    parser.add_argument("--artifact", required=True,
                        help="skill:<path to SKILL.md or its directory>, or text:<path>")
    parser.add_argument("--max-atoms", type=positive_int, default=None, dest="max_atoms",
                        help="Atom cap per artifact (default 120). The measurement is "
                             "LLM-call bound: atoms × probes × arms × (answer + grade). "
                             "Unset by default so that passing the default value explicitly "
                             "still overrides a --tier cap")
    parser.add_argument("--all-atoms", action="store_true", dest="all_atoms",
                        help="Extract every atom (opt-in: this is the expensive sweep)")
    parser.add_argument("--budgets", type=float, nargs="+", default=[0.75, 0.5],
                        help="Target token fractions for shortening candidates")
    parser.add_argument("--cache-dir", default=None, dest="cache_dir",
                        help="Disk cache for retention adjudications")
    parser.add_argument("--seed", type=int, default=0,
                        help="Seed for the shuffled control and random selection, so both "
                             "are reproducible across runs")


def _add_model_args(parser: argparse.ArgumentParser, positive_int) -> None:
    parser.add_argument("--model", default=DEFAULT_ANSWER_MODEL,
                        help="The model under measurement — novelty is a property of the "
                             "(artifact, model) pair, not of the file")
    parser.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=PROVIDERS)
    parser.add_argument("--judge-model", default=None, dest="judge_model",
                        help="Grading model; must differ from --model (default: "
                             f"{DEFAULT_JUDGE_PROVIDER}/{DEFAULT_JUDGE_MODEL}, or "
                             f"{DEFAULT_GENERATOR_PROVIDER}/{DEFAULT_GENERATOR_MODEL} when "
                             "--model is that judge). A --judge-model without "
                             "--judge-provider runs on --provider")
    parser.add_argument("--judge-provider", default=None, dest="judge_provider", choices=PROVIDERS,
                        help="Provider for the judge. Without --judge-model, the default judge "
                             "that runs on this provider; if none does, the run exits asking "
                             "for --judge-model")
    parser.add_argument("--extract-model", default=None, dest="extract_model",
                        help="Extractor/adjudicator model (defaults to --judge-model, so the "
                             "answering model never writes its own answer key)")
    parser.add_argument("--extract-provider", default=None, dest="extract_provider",
                        choices=PROVIDERS)
    parser.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens",
                        help="Output-token ceiling. Raise for reasoning models, which can "
                             "spend the provider default on reasoning and return empty text")
    parser.add_argument("--arms", default="no_artifact,full",
                        help="Arms to run: no_artifact,full[,placebo][,shuffled]")
    parser.add_argument("--placebo-text", default=None, dest="placebo_text",
                        help="File of domain-mismatched text for the placebo control: it "
                             "separates 'this artifact carries knowledge' from 'this model "
                             "reacts to any long context'")
    parser.add_argument("--concurrency", type=positive_int, default=5)
    parser.add_argument("--allow-self-judge", action="store_true", dest="allow_self_judge",
                        help="Let the answering model grade itself. Inflates scores; recorded "
                             "as a warning on the artifact")
    parser.add_argument("--no-paraphrase", action="store_true", dest="no_paraphrase")
    parser.add_argument("--no-reversal", action="store_true", dest="no_reversal")
    _add_spend_cap(parser)


def _clients(args: argparse.Namespace):
    """Build the three role clients, defaulting so roles never silently collapse."""
    if args.judge_model:
        judge_model, judge_provider = args.judge_model, args.judge_provider or args.provider
    else:
        judge_model, judge_provider = _default_judge(args.model, args.judge_provider)
    extract_model = args.extract_model or judge_model
    extract_provider = args.extract_provider or judge_provider
    return (
        _client(extract_model, extract_provider, "extractor", args),
        _client(args.model, args.provider, "answerer", args),
        _client(judge_model, judge_provider, "judge", args),
    )


def _client(model: str, provider: str, role: str, args: argparse.Namespace):
    from dokimi.novelty.client import LLMClient

    return LLMClient(
        model=model, provider=provider, role=role,
        max_tokens=getattr(args, "max_tokens", None),
        cap=_spend_cap(args),
    )


def _spend_cap(args: argparse.Namespace):
    """The invocation's one ``--max-usd`` cap, shared by every role client."""
    from dokimi.novelty.client import SpendCap

    limit = getattr(args, "max_usd", None)
    if limit is None:
        return None
    if getattr(args, "_spend_cap", None) is None:
        args._spend_cap = SpendCap(limit_usd=limit)
    return args._spend_cap


def _stops_at_cap(command):
    """Turn a reached spend cap into a clean exit instead of a traceback."""
    from dokimi.novelty.client import SpendCapReached

    @functools.wraps(command)
    def run(args: argparse.Namespace) -> None:
        try:
            command(args)
        except SpendCapReached as exc:
            raise SystemExit(str(exc)) from None

    return run


def _usd(text: str) -> float:
    """A positive, finite USD amount (``0``, negatives, ``nan`` and ``inf`` would disable the cap)."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of USD, got {text!r}")
    return value


def _add_spend_cap(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-usd", type=_usd, default=None, dest="max_usd",
                        help="Stop the run once its model calls have cost this much (USD, "
                             "all roles together; calls in flight can overshoot it). A call "
                             "with no known price also stops it; a reply replayed from "
                             "DOKIMI_RESPONSE_CACHE counts $0, so a stopped run can be resumed "
                             "under the same cap. Default: no cap")


def _default_judge(model: str, provider: str | None = None) -> tuple[str, str]:
    """A (model, provider) judge that is not the answerer, when the caller did not name one.

    The default judge, unless it is the model under measurement (by bare name, so a
    Bedrock ``us.anthropic.claude-...`` id counts too); then the default generator,
    which is of the other family. With ``provider`` (an explicit ``--judge-provider``)
    only a default that runs there is taken: its own provider, or Bedrock for a name in
    ``BEDROCK_MODEL_IDS``. Pairing the provider with a model of another provider would
    send e.g. ``bedrock/gpt-6.1-sol``, so with no such default the run exits instead.
    """
    bare = model.rsplit("/", 1)[-1].rsplit("anthropic.", 1)[-1]
    candidates = [(m, p) for m, p in ((DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER),
                                      (DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER))
                  if m != bare]
    if provider is None:
        return candidates[0]
    for m, p in candidates:
        if p == provider or (provider == "amazon-bedrock" and m in BEDROCK_MODEL_IDS):
            return m, provider
    raise SystemExit(
        f"--judge-provider {provider}: no default judge runs there other than the model "
        f"under measurement ({model}); pass --judge-model as well")


def _arms(spec: str) -> list[str]:
    arms = [part.strip() for part in spec.split(",") if part.strip()]
    if "no_artifact" not in arms:
        # Without the baseline there is no pair, and without a pair every outcome is a
        # level — which is the one thing the judge agreement does not support.
        raise SystemExit("--arms must include no_artifact: the 2x2 is a paired comparison")
    if "full" not in arms:
        raise SystemExit("--arms must include full: the headline 2x2 pairs it with no_artifact")
    return arms


def _atom_cap(args: argparse.Namespace) -> int:
    """The atom cap, resolving the unset sentinel to the documented default."""
    from dokimi.novelty.tiers import DEFAULT_MAX_ATOMS

    return DEFAULT_MAX_ATOMS if args.max_atoms is None else args.max_atoms


def _digest(ref: str, *, bundle=None) -> str | None:
    """Digest of what a run measured, so one field name means one hash.

    With a bundle in hand this is the digest of exactly the measured files, which is the
    only form that can separate an entry-point run from a bundle run of the same skill
    (hashing the directory gives both the same hash) and that never covers sibling files a
    file-ref run never read. Without one it falls back to the subject scorecard's
    path-based digest, so a `text:` artifact and the older call sites keep working.
    """
    if bundle is not None:
        return bundle.measured_digest()
    from dokimi.subjects.scorecard import _digest_ref

    path = Path(ref.removeprefix("skill:").removeprefix("text:"))
    return _digest_ref(str(path), base=Path.cwd()) if path.exists() else None


def _default_dir(ref: str) -> Path:
    """Where results land when the caller named no output path."""
    return Path("results/novelty") / _safe(ref)


def _safe(ref: str) -> str:
    """A directory name from an artifact reference (`skill:a/b` -> `skill-a-b`)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in ref).strip("-.")
    return safe or "artifact"


def _print_headline(artifact: dict) -> None:
    paired = artifact["paired"]
    print(
        f"added: {paired['lenient']['added']} (lenient) / {paired['strict']['added']} (strict), "
        f"already knew: {paired['lenient']['redundant']}, "
        f"degraded: {paired['lenient']['degraded']}, "
        f"dead tokens: {artifact['spans'].get('frac_carrying_nothing')}"
    )
    for warning in artifact.get("warnings", []):
        print(f"WARNING {warning}", file=sys.stderr)
