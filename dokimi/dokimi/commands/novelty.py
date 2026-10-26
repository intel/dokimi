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
from typing import TYPE_CHECKING, NoReturn

from dokimi.commands.inputs import read_text_or_exit
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)

if TYPE_CHECKING:
    from dokimi.novelty.tiers import RunSettings

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

    from dokimi.novelty.spans import segment

    artifact_bundle = _bundle_from_args(args)
    settings = _settings(args)
    record = settings.tier_record()
    if record:
        print(f"Tier `{args.tier}`: " + ", ".join(
            f"{k}={v}" for k, v in sorted(settings.values.items())), file=sys.stderr)
        for line in record["forfeits"]:
            print(f"  forfeits: {line}", file=sys.stderr)
        if record["overrides"]:
            print(f"  overridden by flags: {sorted(record['overrides'])}", file=sys.stderr)

    if args.estimate_only:
        estimate = settings.estimate(len(segment(artifact_bundle.text)),
                                     budgets=tuple(args.budgets), n_arms=len(_arms(args.arms)))
        print(json.dumps(estimate, indent=2))
        print("Estimate only, nothing was measured. ~4 calls per paired probe is the "
              "measured shape; batch sizes and retries move the real figure.",
              file=sys.stderr)
        if args.max_usd is None:
            print("The estimate counts calls, not dollars: what a call costs depends on the "
                  "artifact's length and the models' prices. Pass --max-usd to stop the run "
                  "at a dollar figure.", file=sys.stderr)
        return

    placebo_text = read_text_or_exit(args.placebo_text, "--placebo-text")
    extractor, answerer, judge = _clients(args)
    config = AnalyzeConfig(
        artifact_ref=args.artifact,
        artifact_kind=artifact_bundle.kind,
        name=artifact_bundle.name,
        description=artifact_bundle.description,
        frontmatter=artifact_bundle.frontmatter,
        arms=_arms(args.arms),
        budgets=tuple(args.budgets),
        concurrency=args.concurrency,
        allow_self_judge=args.allow_self_judge,
        placebo_text=placebo_text,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        seed=args.seed,
        include_resources=args.include_resources,
        resource_roles=tuple(args.resource_roles) if args.resource_roles else None,
        **settings.config_fields(),
    )
    try:
        result = analyze(artifact_bundle.text, config,
                         extractor=extractor, answerer=answerer, judge=judge,
                         artifact_bundle=artifact_bundle)
    except (SelfJudgeError, ExtractionError) as exc:
        raise SystemExit(str(exc)) from None

    out_dir = Path(args.out_dir) if args.out_dir else _default_dir(args.artifact)
    out_json = Path(args.out_json) if args.out_json else out_dir / "novelty.json"
    save_artifact("novelty", result.artifact, out_json)
    out_md = out_json.with_suffix(".md")
    out_md.write_text(render_novelty_markdown(result.artifact), encoding="utf-8")
    title = f"{artifact_bundle.name or args.artifact} × {answerer.model}"
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
    settings = _settings(args)
    extractor = _client(args.extract_model or args.model, args.provider, "extractor", args)
    spans = segment(artifact_bundle.text)
    cap = settings.values["max_atoms"]
    extraction = ExtractionReport()
    try:
        atoms = extract_atoms(spans, client=extractor, cap=cap, report=extraction)
    except ExtractionError as exc:
        raise SystemExit(str(exc)) from None
    for warning in extraction.warnings(len(spans)):
        print(f"WARNING {warning}", file=sys.stderr)
    if settings.values["verify_faithfulness"]:
        verify_atoms(atoms, spans, client=extractor)
    out = Path(args.out) if args.out else _default_dir(args.artifact) / "atoms.json"
    save_atoms(atoms, spans, out, extractor=extractor.model, cap=cap,
               bundle=artifact_bundle.summary(), extraction=extraction)
    print(f"OK Saved {len(atoms)} atoms over {len(spans)} spans: {out}")
    print(json.dumps(span_summary(spans, atoms, atom_cap=cap), indent=2))


def cmd_novelty_probe(args: argparse.Namespace) -> None:
    """Run the paired probes against a saved atom set and print the 2×2."""
    from dokimi.novelty.atoms import load_atoms_or_exit
    from dokimi.novelty.bundle import check_answer_key
    from dokimi.novelty.measure import SelfJudgeError, run_measurement
    from dokimi.novelty.probes import build_probe_set
    from dokimi.novelty.report import build_probe_record

    atoms, saved_spans, saved = load_atoms_or_exit(args.atoms)
    _warn_unread(saved_spans, "the atoms probed here leave out whatever those spans assert")
    verdict = check_answer_key(args.artifact, saved.get("bundle"), source=args.atoms)
    if not verdict.ok:
        raise SystemExit(verdict.message)
    for note in verdict.notes:
        print(note, file=sys.stderr)
    settings = _settings(args)
    placebo_text = read_text_or_exit(args.placebo_text, "--placebo-text")
    extractor, answerer, judge = _clients(args)
    if saved.get("extractor") == answerer.model:
        print(f"WARNING: {answerer.model} extracted these atoms, so the model under measurement "
              "wrote its own answer key; extract with a different --extract-model",
              file=sys.stderr)
    paraphrases, reversals = settings.values["paraphrases"], settings.values["reversals"]
    probe_set = build_probe_set(
        atoms, client=extractor if paraphrases or reversals else None,
        paraphrases=paraphrases, reversals=reversals,
    )
    for kind, row in sorted(probe_set.fidelity.items()):
        if row["rejected"]:
            print(f"WARNING: dropped {row['rejected']} of {row['generated']} generated {kind} "
                  f"wordings that failed the fidelity check "
                  f"({', '.join(f'{r}={n}' for r, n in sorted(row['reasons'].items()))})",
                  file=sys.stderr)
    try:
        result = run_measurement(
            atoms, probe_set.probes, verdict.text, answerer=answerer, judge=judge,
            arms=_arms(args.arms), concurrency=args.concurrency,
            allow_self_judge=args.allow_self_judge, seed=args.seed,
            placebo_text=placebo_text,
        )
    except (SelfJudgeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None

    payload = build_probe_record(
        artifact_ref=args.artifact, model=answerer.model, judge_model=judge.model,
        probed_bundle=verdict.bundle, atoms_bundle=saved.get("bundle"),
        probe_set=probe_set, measure_result=result,
    )
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
    from dokimi.novelty.items import ProbeFileError, item_analysis, load_probe_cells

    try:
        cells, warnings = load_probe_cells(args.probes)
    except ProbeFileError as exc:
        raise SystemExit(str(exc)) from None
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    payload = item_analysis(cells, arm=args.arm)
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
    if args.repeats > 1 and not args.model:
        raise SystemExit(
            "--repeats needs --model: the lexical floor is deterministic, so repeating it "
            "measures nothing"
        )
    from dokimi.novelty.routing import (
        Candidate,
        RoutingInputError,
        catalog_competitors,
        check_field,
        headings_as_queries,
        lexical_routing,
        load_candidate,
        load_queries,
        model_routing,
    )

    def card(ref: str) -> tuple[Candidate, str]:
        candidate, body, warning = load_candidate(ref)
        if warning:
            print(f"WARNING: {warning}", file=sys.stderr)
        return candidate, body

    try:
        gold, body = card(args.artifact)
        competitors = [card(ref)[0] for ref in args.competitors or []]
        if args.catalog:
            catalog = catalog_competitors(args.catalog,
                                          {gold.name, *(c.name for c in competitors)})
            no_description = [c.name for c in catalog if not c.description]
            if no_description:
                print(f"WARNING: {len(no_description)} catalog skill(s) have no description, "
                      f"so their card is a name only: {', '.join(sorted(no_description))}",
                      file=sys.stderr)
            print(f"--catalog {args.catalog}: {len(catalog)} competitors", file=sys.stderr)
            competitors += catalog
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"cannot load an artifact: {exc}") from None
    # Checked before the query file is read, so the message is about the real problem.
    try:
        check_field(gold, competitors)
    except RoutingInputError as exc:
        raise SystemExit(str(exc)) from None

    self_derived = False
    if args.queries:
        try:
            queries = load_queries(args.queries)
        except RoutingInputError as exc:
            raise SystemExit(str(exc)) from None
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
    candidates = [gold, *competitors]
    lexical = dict(lexical_routing(candidates, queries, gold=0),
                   queries_self_derived=self_derived)
    out = Path(args.out) if args.out else _default_dir(args.artifact) / "routing.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not lexical["n_queries_scored"]:
        _refuse_no_queries(args, queries, self_derived, out, len(candidates))

    payload = {
        "artifact": args.artifact,
        "status": "ok",
        "n_candidates": len(candidates),
        "queries_self_derived": self_derived,
        "lexical": lexical,
    }
    if args.model:
        selector = _client(args.model, args.provider, "selector", args)
        payload["model"] = dict(
            model_routing(candidates, queries, client=selector, gold=0, seed=args.seed,
                          repeats=args.repeats),
            queries_self_derived=self_derived,
        )

    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"OK Saved routing measurement: {out}")

    _print_routing_summary(payload, len(competitors), self_derived)

def _print_routing_summary(payload: dict, n_competitors: int, self_derived: bool) -> None:
    """The lexical line, the model line and their caveats, printed after a routing run."""
    # Every rate is printed with the number of queries behind it, and a self-derived one
    # carries the triage tag on the same line, so a copied figure cannot lose either.
    tag = "  triage: self-derived" if self_derived else ""
    lexical = payload["lexical"]
    print(f"lexical  R@1 {lexical['recall_at_1']}  R@3 {lexical['recall_at_3']}  "
          f"MRR {lexical['mrr']}  n_queries {lexical['n_queries_scored']} of "
          f"{lexical['n_queries_given']} given, vs {n_competitors} competitors{tag}"
          + ("  (R@3 needs more than 3 candidates to mean anything)"
             if lexical["recall_at_3"] is None else ""))
    if lexical["n_queries_scored"] < 5:
        n_scored = lexical["n_queries_scored"]
        noun = "query" if n_scored == 1 else "queries"
        print(f"WARNING: only {n_scored} {noun} scored; one query moves "
              "a rate by 20 points or more, so do not quote it as a routing rate")
    if "model" in payload:
        row = payload["model"]
        print(f"model    selection {row['selection_rate']}  "
              f"({row['selection_rate_among_answered']} among answered; "
              f"{row['n_abstained']} abstained, {row['n_failed']} failed)  "
              f"[{row['model']}]  n_queries {row['n_queries']} given{tag}")
        if row["n_abstained"]:
            of_calls = f" of {row['n_calls']} calls" if row["repeats"] > 1 else ""
            print(f"{row['n_abstained']}{of_calls} abstained or unparseable: the raw reply "
                  "is under per_query[].raw_reply in the output")
        if row["noise"]:
            noise = row["noise"]
            print(f"noise    {row['repeats']} repeats per query: mean agreement "
                  f"{noise['mean_agreement']}, {noise['unstable_queries']} queries picked "
                  f"differently across repeats; selection by repeat "
                  f"{noise['selection_rate_by_repeat']}, half range "
                  f"{noise['half_range']} (a crude sampling floor)")
        print("The model figure is an upper bound on harness behaviour: a harness that "
              "truncates descriptions in its own listing selects on less text.")
    if self_derived:
        print("Self-derived queries: read as triage only.")



def _refuse_no_queries(args: argparse.Namespace, queries: list[str], self_derived: bool,
                       out: Path, n_candidates: int) -> NoReturn:
    """Write the ``no_queries`` record, then exit saying why no query was scored."""
    # Written too, so a batch run leaves a record of why it has no rate.
    if not queries and self_derived:
        reason = (f"no section heading of two or more words outside code fences in "
                  f"{args.artifact}; headings inside fenced code are not requests")
    elif not queries:
        reason = f"--queries {args.queries} holds no non-blank query"
    else:
        reason = (f"none of the {len(queries)} queries has a content word, since words "
                  "like a, the, how do not count")
    out.write_text(json.dumps({"artifact": args.artifact, "status": "no_queries",
                               "reason": reason, "queries_self_derived": self_derived,
                               "n_candidates": n_candidates}, indent=2),
                   encoding="utf-8")
    raise SystemExit(f"no usable queries: {reason}. Pass --queries <file> with requests "
                     f"written independently of the artifact, one per line or a JSON "
                     f"list. Wrote {out} with status no_queries")


def cmd_novelty_shorten(args: argparse.Namespace) -> None:
    """Score shortening candidates on the five axes and optionally write the best one."""
    from dokimi.novelty.artifact import load_artifact_or_exit
    from dokimi.novelty.atoms import load_atoms_or_exit
    from dokimi.novelty.bundle import entry_point_only
    from dokimi.novelty.retention import RetentionOracle, error_warning
    from dokimi.novelty.shorten import build_candidates, pick_best

    atoms, spans, saved = load_atoms_or_exit(args.atoms)
    _kind, _name, _description, frontmatter, _body = load_artifact_or_exit(args.artifact)
    try:
        spans, atoms, note = entry_point_only(saved.get("bundle"), spans, atoms)
    except ValueError as exc:
        raise SystemExit(f"{args.atoms}: {exc}") from None
    if note:
        print(note, file=sys.stderr)
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
    from dokimi.commands.novelty_view_inputs import load_view_inputs
    from dokimi.novelty.attribute import attribute_spans
    from dokimi.novelty.report import render_annotated_html, render_annotated_markdown

    artifact, atoms, spans = load_view_inputs(args.novelty, args.atoms)
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
    note = artifact_bundle.coverage_note()
    if note:
        print(f"NOTE: {note}", file=sys.stderr)
    return artifact_bundle


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
                                "SKILL.md under it except the artifact itself (a template/ "
                                "copy and a byte-identical mirror that loads under the same "
                                "skill name are left out), i.e. the siblings a harness "
                                "lists next to it. Combines with --competitors")
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
    routing_p.add_argument("--repeats", type=positive_int, default=1,
                           help="Ask the model this many times per query, with the same card "
                                "order each time, and report per-query agreement and the "
                                "run-to-run noise floor. Needs --model; costs N times the calls")
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
    from dokimi.novelty.client import default_judge

    if args.judge_model:
        judge_model, judge_provider = args.judge_model, args.judge_provider or args.provider
    else:
        try:
            judge_model, judge_provider = default_judge(args.model, args.judge_provider)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
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


def _settings(args: argparse.Namespace) -> RunSettings:
    """The run settings these flags resolve to; a flag the command lacks is unset."""
    from dokimi.novelty.tiers import FLAG_DEFAULTS, resolve_settings

    return resolve_settings(getattr(args, "tier", None), {
        flag: getattr(args, flag, default) for flag, default in FLAG_DEFAULTS.items()})


def _arms(spec: str) -> tuple[str, ...]:
    from dokimi.novelty.measure import parse_arms

    try:
        return parse_arms(spec)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


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
    _print_coverage(artifact.get("bundle") or {})
    for warning in artifact.get("warnings", []):
        print(f"WARNING {warning}", file=sys.stderr)


def _print_coverage(bundle: dict) -> None:
    """How much of the artifact's readable words this run read, on stdout.

    The share is stated on stdout because the headline is what a user copies. A run under
    the floor also says so on stdout: a stderr-only warning is one a user piping the
    headline never sees, and a rate quoted off half-read skill is not a property of it.
    """
    from dokimi.novelty.bundle import MEASURED_SHARE_WARN_AT, Coverage

    coverage = Coverage.from_record(bundle)
    if coverage is None or not coverage.denominator("readable"):
        return
    share = coverage.measured_share
    scope = "" if coverage.include_resources else " (entry point only)"
    # The warnings below need readable words outside the entry point, so the flag always adds some.
    rerun = "" if coverage.include_resources else "; pass --include-resources to measure the bundled files"
    if share is None:
        print("coverage: unknown (a bundled file did not decode, so its words are not "
              f"counted){scope}")
        # The share is unknowable, but a floor on what was left unread is not: for an
        # entry-point run it is the stderr warning's figure, and a capped bundle run has one too.
        if coverage.counted_unmeasured_share >= 1 - MEASURED_SHARE_WARN_AT:
            part = "what this run read is" if coverage.include_resources else "the entry point is"
            print(f"WARNING: {part} half or less of the artifact's readable words, so every "
                  f"rate here describes that part, not the artifact{rerun}")
        return
    # Floored on the 4-place share, in integers: a float product floors 0.29 to 28, and
    # 49.99% must not print as "50%" beside a "below 50%" warning. A true 49.996% is already
    # 0.5 by the time it is stored.
    pct = "<1%" if 0 < share < 0.01 else f"{round(share * 10000) // 100}%"
    print(f"coverage: {pct} of the artifact's readable words were measured{scope}")
    if share <= MEASURED_SHARE_WARN_AT:
        print(f"WARNING: only {pct} of the artifact's readable words were measured, so every "
              f"rate here describes that part, not the artifact{rerun}")
