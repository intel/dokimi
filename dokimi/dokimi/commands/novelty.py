# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""novelty subcommand group: what does this artifact add to this model?

Standalone by design. `skill novelty analyze` needs an artifact and a model — no question set,
no task suite, no benchmark run — because the first user is an author editing a skill,
not a CI job. The pieces (`skill novelty atoms|probe`, `skill shorten`, `view`) exist so an
expensive stage can be paid for once and reused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from dokimi.commands.inputs import placebo_text_for_arms_or_exit
from dokimi.commands.novelty_run import (add_judge2_args, add_spend_cap, judge2_client,
                                         exit_if_analyze_partial, stamp, usable_cache_dir)
from dokimi.commands.novelty_routing_inputs import (
    checked_out_path,
    load_all_vs_all_field,
    load_routing_field,
)
from dokimi.commands.novelty_routing_report import (
    check_all_vs_all_args,
    check_all_vs_all_field,
    drop_queries_left_out,
    exit_partial_all_vs_all,
    field_record,
    no_competitors_conflict,
    no_queries_reason,
    print_all_vs_all_estimate,
    print_all_vs_all_summary,
    print_routing_estimate,
    print_routing_summary,
    refuse_estimate_without_queries,
    routing_queries,
    save_result,
    stop_if_partial,
    warn_blind_skills,
)
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)
from dokimi.llm import ANSWER_PROVIDERS as PROVIDERS
from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.novelty.tiers import RunSettings


def cmd_novelty_analyze(args: argparse.Namespace) -> None:
    """Run the full pipeline over one artifact and write the novelty artifact + views."""
    from dokimi.novelty import cost
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

    from dokimi.novelty.tiers import FITTED_TOKENS_MAX
    from dokimi.commands.novelty_budget import print_run_estimate, run_estimate

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
        estimate, n_tokens = run_estimate(args, settings, artifact_bundle.text, cost.estimate_usd)
        # Every arm but the baseline is priced as carrying the artifact; a placebo of another
        # length is not read.
        if n_tokens > FITTED_TOKENS_MAX:
            estimate["usd_extrapolated"] = True
            print(f"expected_usd extrapolates: the artifact has {n_tokens} tokens and the "
                  f"profiles were fitted on artifacts up to {FITTED_TOKENS_MAX}; the projection "
                  "ran low as the artifact grew", file=sys.stderr)
        print(json.dumps(estimate, indent=2))
        print("Estimate only, nothing was measured. ~4 calls per paired probe is the "
              "measured shape; batch sizes and retries move the real figure.",
              file=sys.stderr)
        if args.max_usd is None:
            print(
                "expected_usd prices the calls by role at list prices, from a token profile "
                "fitted on measured runs: a longer artifact or longer answers cost more, and "
                "a model with no price gives null. Pass --max-usd to stop the run at a "
                "dollar figure.",
                file=sys.stderr,
            )
        return
    cache_dir = usable_cache_dir(args.cache_dir)
    print_run_estimate(args, settings, artifact_bundle.text, cost.estimate_usd)

    placebo_text = placebo_text_for_arms_or_exit(args.placebo_text, _arms(args.arms))
    extractor, answerer, judge = cost.clients(args)
    judge2 = judge2_client(args, answerer, judge, cost.client)
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
        placebo_text=placebo_text, context_framing=args.context_framing,
        cache_dir=cache_dir,
        seed=args.seed,
        include_resources=args.include_resources,
        resource_roles=tuple(args.resource_roles) if args.resource_roles else None,
        **settings.config_fields(),
    )
    try:
        result = analyze(artifact_bundle.text, config,
                         extractor=extractor, answerer=answerer, judge=judge,
                         artifact_bundle=artifact_bundle)
    except SelfJudgeError as exc:  # the judge is the answering model: the flags contradict
        raise InputError(str(exc)) from exc
    except (ExtractionError, ValueError) as exc:  # a model failure under way
        raise SystemExit(str(exc)) from None
    out_dir = Path(args.out_dir) if args.out_dir else _default_dir(args.artifact)
    out_json = Path(args.out_json) if args.out_json else out_dir / "novelty.json"
    exit_if_analyze_partial(result, out_json)  # a run the cap stopped keeps what it paid for
    stamp(result.artifact, args, result.measure_result, answerer=answerer, judge=judge, judge2=judge2)
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
    from dokimi.novelty import cost
    from dokimi.commands.novelty_budget import save_atoms_run, verify_or_partial
    from dokimi.novelty.atoms import ExtractionError, ExtractionReport, extract_atoms, sample_atoms
    from dokimi.novelty.report import cap_warning
    from dokimi.novelty.spans import segment, span_summary

    artifact_bundle = _bundle_from_args(args)
    settings = _settings(args)
    extractor = cost.client(args.extract_model or args.model, args.provider, "extractor", args)
    spans = segment(artifact_bundle.text)
    cap = settings.values["max_atoms"]
    extraction = ExtractionReport()
    try:
        atoms = extract_atoms(spans, client=extractor, cap=cap, report=extraction)
    except ExtractionError as exc:
        raise SystemExit(str(exc)) from None
    sample = sample_atoms(atoms, spans, cap)  # what the run probes; atoms.json holds every atom
    # The cap warning analyze records; probe and shorten, which read atoms.json, do not repeat it.
    sampled = [cap_warning(extraction.as_dict())] if extraction.sampled else []
    # warnings() leaves out the spans a cap left unread: the exit message names them.
    for warning in extraction.warnings(len(spans)) + sampled:
        print(f"WARNING {warning}", file=sys.stderr)
    partial = extraction.partial  # a cap that stopped extraction keeps what it read
    if settings.values["verify_faithfulness"] and partial is None:
        partial = verify_or_partial(sample, spans, extractor)
    out = Path(args.out) if args.out else _default_dir(args.artifact) / "atoms.json"
    save_atoms_run(atoms, spans, out, partial, extractor=extractor.model,
                   extractor_provider=extractor.provider, cap=cap,
                   bundle=artifact_bundle.summary(), extraction=extraction, sample=sample)
    print(f"OK Saved {len(atoms)} atoms ({len(sample)} sampled) over {len(spans)} spans: {out}")
    print(json.dumps(span_summary(spans, atoms), indent=2))


def cmd_novelty_probe(args: argparse.Namespace) -> None:
    """Run the paired probes against a saved atom set and print the 2×2."""
    from dokimi.novelty import cost
    from dokimi.commands.novelty_atoms_file import (measured_atoms, sampling_record,
                                                    warn_answer_key, warn_unread)
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.novelty.answer_key_check import check_answer_key
    from dokimi.novelty.measure import SelfJudgeError, check_self_judge, run_measurement
    from dokimi.novelty.probes import build_probe_set
    from dokimi.novelty.report import build_probe_record
    from dokimi.commands.novelty_budget import save_probe_measure

    atoms, saved_spans, saved = load_atoms_record(args.atoms)
    atoms = measured_atoms(args, atoms, saved, "probe")
    warn_unread(saved_spans, "the atoms probed here leave out whatever those spans assert")
    verdict = check_answer_key(args.artifact, saved.get("bundle"), source=args.atoms)
    if not verdict.ok:
        raise InputError(verdict.message)
    for note in verdict.notes:
        print(note, file=sys.stderr)
    settings = _settings(args)
    placebo_text = placebo_text_for_arms_or_exit(args.placebo_text, _arms(args.arms))
    extractor, answerer, judge = cost.clients(args)
    warn_answer_key(saved, answerer, judge)
    try:  # before the paraphrase calls below, which are paid
        check_self_judge(judge, answerer, args.allow_self_judge)
        judge2 = judge2_client(args, answerer, judge, cost.client)
    except SelfJudgeError as exc:
        raise InputError(str(exc)) from exc
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
            placebo_text=placebo_text, framing=args.context_framing,
        )
    except ValueError as exc:  # a model failure under way
        raise SystemExit(str(exc)) from None

    payload = build_probe_record(
        artifact_ref=args.artifact, model=answerer.model, judge_model=judge.model,
        probed_bundle=verdict.bundle, atoms_bundle=saved.get("bundle"),
        probe_set=probe_set, measure_result=result,
        sampling=sampling_record(args, atoms, saved), framing=args.context_framing,
    )
    stamp(payload, args, result, answerer=answerer, judge=judge, judge2=judge2)
    out = Path(args.out) if args.out else _default_dir(args.artifact) / "probes.json"
    # A run the cap stopped keeps the answers it paid for, as a partial result (and exits).
    save_probe_measure(payload, out, result)
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
        raise InputError(str(exc)) from exc
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


def _cmd_routing_all_vs_all(args: argparse.Namespace) -> None:
    """Every skill in the field is the gold in turn: confusion matrix and attractors."""
    from dokimi.novelty import cost
    from dokimi.novelty.all_vs_all import all_vs_all, load_queries_by_skill
    from dokimi.novelty.client import roll_up_cost
    from dokimi.novelty.routing import tokenize

    check_all_vs_all_args(args)
    left_out: list[str] = []
    dropped: dict[str, set[str]] = {}
    conflicts: dict[str, list[str]] = {}
    unloaded: dict[str, str] = {}
    field = load_all_vs_all_field(args, left_out, dropped, conflicts, unloaded)
    try:
        queries_by_skill = load_queries_by_skill(args.queries_by_skill)
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read the queries: {exc}") from exc
    check_all_vs_all_field(args, field, dropped, conflicts)
    queries_by_skill, dropped_queries = drop_queries_left_out(queries_by_skill, field, dropped,
                                                              unloaded)
    if args.estimate_only:
        print_all_vs_all_estimate(args, field, queries_by_skill)
        return
    selector = cost.client(args.model, args.provider, "selector", args) if args.model else None
    if selector is not None:
        # One call per query with a content word and per repeat, each sending every card.
        calls = sum(bool(tokenize(q)) for qs in queries_by_skill.values() for q in qs)
        calls *= args.repeats
        print(
            f"all-vs-all: --model makes {calls} model calls, one per query and repeat, over "
            f"{len(field)} cards each",
            file=sys.stderr,
        )
    result = all_vs_all(field, queries_by_skill, client=selector, seed=args.seed,
                        repeats=args.repeats)
    without = [c.name for c in field if c.name not in queries_by_skill]
    if without:
        print(f"WARNING: {len(without)} skill(s) in the field have no queries, so they only "
              f"compete: {', '.join(sorted(without))}", file=sys.stderr)
    warn_blind_skills(result, queries_by_skill)

    model_arm = result.get("model") or {}
    failed = model_arm.get("n_failed", 0)
    sent = sum(model_arm.get("n_scored", {}).values()) + failed  # contentless ones are not sent
    if failed:
        print(f"WARNING: {failed} model call(s) failed (first error: "
              f"{model_arm['first_error']}); failed calls are left out of model-arm scoring, "
              "and a query with a failed repeat out of the repeat-noise rates", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mode": "all_vs_all", "queries_by_skill": args.queries_by_skill,
               "n_candidates": len(field), "result": result}
    payload["field"] = field_record(args, field, left_out, dropped_queries)
    if selector is not None:
        payload["cost"] = roll_up_cost([selector])
    out = save_result(payload, out, model_arm)
    summary = (result, len(field), len(queries_by_skill), model_arm, payload.get("cost"))
    stop_if_partial(payload, out, lambda: print_all_vs_all_summary(*summary))
    if failed:
        exit_partial_all_vs_all(model_arm, payload.get("cost"), failed, sent, out)
    print(f"OK Saved all-vs-all routing: {out}")
    print_all_vs_all_summary(*summary)


def cmd_routing(args: argparse.Namespace) -> None:
    """Can a skill (or each skill under a root) be selected against its competitors? Lexical floor, model on top."""
    from dokimi.novelty import cost
    if args.repeats > 1 and not args.model:
        raise InputError("--reps needs --model: the lexical floor is deterministic, so repeating it measures nothing")
    from dokimi.novelty.routing import (
        check_field,
        lexical_routing,
        model_routing,
    )

    if args.all_vs_all:
        return _cmd_routing_all_vs_all(args)
    gold, body, competitors, left_out, conflicts = load_routing_field(args)
    # Checked before the query file is read, so the message is about the real problem.
    if not competitors and conflicts:
        raise InputError(no_competitors_conflict(args, conflicts))
    check_field(gold, competitors)

    queries, self_derived, queries_source = routing_queries(args, body)
    candidates = [gold, *competitors]
    if args.estimate_only:
        refuse_estimate_without_queries(args, queries, self_derived)
        print_routing_estimate(args, candidates, queries)
        return
    lexical = dict(lexical_routing(candidates, queries, gold=0),
                   queries_self_derived=self_derived)
    out = checked_out_path(args.out, "routing", default=str(_default_dir(args.artifact) / "routing.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not lexical["n_queries_scored"]:
        _refuse_no_queries(args, queries, self_derived, out, len(candidates), queries_source)

    payload = {
        "artifact": args.artifact,
        "status": "ok",
        "n_candidates": len(candidates),
        "queries_self_derived": self_derived,
        "queries_source": queries_source,
        "field": field_record(args, competitors, left_out, gold=gold),
        "lexical": lexical,
    }
    if args.model:
        from dokimi.novelty.client import roll_up_cost

        selector = cost.client(args.model, args.provider, "selector", args)
        payload["model"] = dict(
            model_routing(candidates, queries, client=selector, gold=0, seed=args.seed,
                          repeats=args.repeats),
            queries_self_derived=self_derived,
        )
        payload["cost"] = roll_up_cost([selector])

    out = save_result(payload, out, payload.get("model", {}))
    stop_if_partial(payload, out,
                    lambda: print_routing_summary(payload, len(competitors), self_derived))
    print(f"OK Saved routing measurement: {out}")

    print_routing_summary(payload, len(competitors), self_derived)


def _refuse_no_queries(args: argparse.Namespace, queries: list[str], self_derived: bool,
                       out: Path, n_candidates: int,
                       queries_source: str | None = None) -> NoReturn:
    """Write the ``no_queries`` record, then exit saying why no query was scored."""
    # Written too, so a batch run leaves a record of why it has no rate.
    reason = no_queries_reason(args, queries, self_derived)
    out.write_text(json.dumps({"artifact": args.artifact, "status": "no_queries",
                               "reason": reason, "queries_self_derived": self_derived,
                               "queries_source": queries_source or ("none" if self_derived else None),
                               "n_candidates": n_candidates}, indent=2),
                   encoding="utf-8")
    raise InputError(f"no usable queries: {reason}. Pass --queries <file> with requests written independently "
               f"of the artifact, one per line or a JSON list. Wrote {out} with status no_queries")


def cmd_novelty_shorten(args: argparse.Namespace) -> None:
    """Score shortening candidates on the five axes and optionally write the best one."""
    from dokimi.commands.novelty_atoms_file import (
        measured_atoms, no_candidate_line, score_candidates, warn_unread)
    from dokimi.novelty.artifact import load_artifact_ref
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.novelty.bundle import entry_point_only
    from dokimi.novelty.retention import error_warning
    from dokimi.novelty.shorten import pick_best

    atoms, spans, saved = load_atoms_record(args.atoms)
    _kind, _name, _description, frontmatter, _body = load_artifact_ref(args.artifact)
    try:
        spans, atoms, note = entry_point_only(saved.get("bundle"), spans, atoms)
    except ValueError as exc:
        raise InputError(f"{args.atoms}: {exc}") from exc
    if note:
        print(note, file=sys.stderr)
    warn_unread(spans, "every candidate keeps them, since what they carry is unknown")
    scored = measured_atoms(args, atoms, saved, "shorten")
    candidates, oracle = score_candidates(args, spans, atoms, scored)
    oracle_warning = error_warning(oracle)
    if oracle_warning:
        print(f"WARNING {oracle_warning}", file=sys.stderr)
    print(f"{'strategy':20s} {'budget':>7s} {'tok':>6s} {'know':>6s} "
          f"{'value':>6s} {'prot':>6s} {'lost':>4s} safe")
    for candidate in candidates:
        row = candidate.as_dict()
        print(
            f"{row['strategy']:20s} {row['budget_frac']:7.2f} {row['token_frac']:6.2f} "
            f"{row['knowledge']:6.3f} {row['value']:6.3f} {row['protection']:6.3f} "
            f"{row['dropped_unscored_atoms']:4d} {row['safe']}"
        )
    if args.lexical_only:
        print(
            "NOTE lexical-only oracle: contradictions are invisible to it (0.00 measured "
            "contradiction recall), so `knowledge` here is a floor, not a verdict."
        )
    best = pick_best(candidates, require_safe=not args.allow_unsafe)
    if best is None:
        print(no_candidate_line(candidates, scored, atoms))
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
    from dokimi.commands.novelty_atoms_file import warn_unread
    from dokimi.commands.novelty_view_inputs import load_view_inputs
    from dokimi.novelty.attribute import attribute_spans
    from dokimi.novelty.report import render_annotated_html, render_annotated_markdown

    artifact, atoms, spans = load_view_inputs(args.novelty, args.atoms)
    warn_unread(spans, "they render as `unread`, not as text that carries nothing")
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
        "(added / degraded), render during `skill novelty analyze`, which has the paired runs."
    )


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
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"cannot load --artifact {args.artifact}: {exc}") from exc
    note = artifact_bundle.coverage_note()
    if note:
        print(f"NOTE: {note}", file=sys.stderr)
    return artifact_bundle


def register(sub, positive_int) -> None:
    """Add the `novelty` group: `view`. `routing` moved to `skill routing` and `catalog routing` (#94 M3)."""
    novelty_p = sub.add_parser(
        "novelty",
        help="Measure what an artifact's content adds to a model",
    )
    novelty_sub = novelty_p.add_subparsers(dest="novelty_cmd", required=True)

    view_p = novelty_sub.add_parser("view", help="Re-render the annotated artifact view")
    view_p.add_argument("--novelty", required=True, help="novelty.json from `skill novelty analyze`")
    view_p.add_argument("--atoms", required=True, help="atoms.json from `skill novelty atoms`")
    view_p.add_argument("--format", default="html", choices=["html", "md"])
    view_p.add_argument("--out", default=None)
    view_p.set_defaults(func=cmd_novelty_view)


def _add_bundle_args(parser: argparse.ArgumentParser, positive_int) -> None:
    """Flags that decide *which files of the artifact* are measured.

    On `atoms` as well as `analyze`: the cheap path is `skill novelty atoms` once and
    `skill novelty probe --atoms` per model, so the bundle decision has to be makeable at
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
                        help="Probe every extracted atom (opt-in: the expensive sweep); with "
                             "--atoms, every atom in the file, not only the cap's sample")
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
                        help="Arms to run: no_artifact,full[,placebo][,shuffled]"
                             "[,no_artifact_repeat] (the last is the noise floor)")
    parser.add_argument("--context-framing", default="reference", dest="context_framing",
                        choices=("reference", "neutral"),
                        help="How the context is introduced: as reference material, or as a "
                             "document that may be irrelevant (the placebo framing control)")
    parser.add_argument("--placebo-text", default=None, dest="placebo_text",
                        help="File of domain-mismatched text for the placebo control: it "
                             "separates 'this artifact carries knowledge' from 'this model "
                             "reacts to any long context'")
    parser.add_argument("--concurrency", type=positive_int, default=5)
    parser.add_argument("--allow-self-judge", action="store_true", dest="allow_self_judge",
                        help="Let the answering model grade itself (inflates scores), or --judge2 be"
                             " the first judge's model (its agreement is not independent)")
    add_judge2_args(parser, PROVIDERS)
    parser.add_argument("--no-paraphrase", action="store_true", dest="no_paraphrase")
    parser.add_argument("--no-reversal", action="store_true", dest="no_reversal")
    add_spend_cap(parser)


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
        raise InputError(str(exc)) from exc


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
    _print_coverage(artifact.get("bundle") or {}, artifact.get("extraction"))
    for warning in artifact.get("warnings", []):
        print(f"WARNING {warning}", file=sys.stderr)


def _print_coverage(bundle: dict, extraction: dict | None = None) -> None:
    """How much of the artifact's readable words this run read, on stdout.

    The share is stated on stdout because the headline is what a user copies. A run under
    the floor also says so on stdout: a stderr-only warning is one a user piping the
    headline never sees, and a rate quoted off half-read skill is not a property of it.
    A capped run says the words went into the input, and how many of its atoms were probed.
    """
    from dokimi.novelty.bundle import MEASURED_SHARE_WARN_AT, Coverage
    from dokimi.novelty.report import coverage_reach, floored_percent

    went, reach = coverage_reach(extraction)
    coverage = Coverage.from_record(bundle)
    if coverage is None or not coverage.denominator("readable"):
        return
    share = coverage.measured_share
    scope = "" if coverage.include_resources else " (entry point only)"
    # The warnings below need readable words outside the entry point, so the flag always adds some.
    rerun = "" if coverage.include_resources else "; pass --include-resources to measure the bundled files"
    if share is None:
        print("coverage: unknown (a bundled file did not decode, so its words are not "
              f"counted){scope}{reach}")
        # The share is unknowable, but a floor on what was left unread is not: for an
        # entry-point run it is the stderr warning's figure, and a capped bundle run has one too.
        if coverage.counted_unmeasured_share >= 1 - MEASURED_SHARE_WARN_AT:
            part = "what this run read is" if coverage.include_resources else "the entry point is"
            print(f"WARNING: {part} half or less of the artifact's readable words, so every "
                  f"rate here describes that part, not the artifact{rerun}")
        return
    pct = floored_percent(share)
    print(f"coverage: {pct} of the artifact's readable words {went}{scope}{reach}")
    if share <= MEASURED_SHARE_WARN_AT:
        print(f"WARNING: only {pct} of the artifact's readable words {went}, so every "
              f"rate here describes that part, not the artifact{rerun}")
