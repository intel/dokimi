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
from typing import TYPE_CHECKING

from dokimi.commands.inputs import placebo_text_for_arms_or_exit
from dokimi.commands.novelty_run import (attach_panel, exit_if_analyze_partial,
                                         judge2_client, panel_clients, save_panel_verdicts, stamp,
                                         usable_cache_dir)
from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.novelty.tiers import RunSettings


def cmd_novelty_analyze(args: argparse.Namespace) -> None:
    """Run the full pipeline over one artifact and write the novelty artifact + views."""
    from dokimi.novelty import cost
    from dokimi.artifacts import save_artifact
    from dokimi.commands._output import write_report_text
    # Import the symbols, not the module: the package re-exports `analyze` as the
    # function, which shadows the submodule of the same name.
    from dokimi.novelty.analyze import AnalyzeConfig, analyze
    from dokimi.novelty.atom_verdicts import save_atom_verdicts, save_run_atoms
    from dokimi.novelty.atoms import ExtractionError
    from dokimi.novelty.measure import SelfJudgeError
    from dokimi.novelty.report import (
        render_annotated_html,
        render_annotated_markdown,
        render_novelty_markdown,
    )

    from dokimi.commands.novelty_budget import print_estimate_only, print_run_estimate

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
        print_estimate_only(args, settings, artifact_bundle.text, cost.estimate_usd)
        return
    cache_dir = usable_cache_dir(args.cache_dir)
    print_run_estimate(args, settings, artifact_bundle.text, cost.estimate_usd)

    placebo_text = placebo_text_for_arms_or_exit(args.placebo_text, _arms(args.arms))
    extractor, answerer, judge = cost.clients(args)
    panel = panel_clients(args, cost.client)
    judge2 = judge2_client(args, answerer, judge, cost.client, panel)
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
                         artifact_bundle=artifact_bundle,
                         panel=panel, aggregate=args.judge_aggregate)
    except SelfJudgeError as exc:  # the judge is the answering model: the flags contradict
        raise InputError(str(exc)) from exc
    except (ExtractionError, ValueError) as exc:  # a model failure under way
        raise SystemExit(str(exc)) from None
    out_dir = Path(args.out_dir) if args.out_dir else _default_dir(args.artifact)
    out_json = Path(args.out_json) if args.out_json else out_dir / "novelty.json"
    exit_if_analyze_partial(result, out_json)  # a run the cap stopped keeps what it paid for
    stamp(result.artifact, args, result.measure_result, answerer=answerer, judge=judge, judge2=judge2)
    save_artifact("novelty", result.artifact, out_json)
    verdicts_path = save_panel_verdicts(result, out_json)
    atom_verdicts_path = save_atom_verdicts(result, out_json)
    atoms_path = save_run_atoms(result, out_json, extractor=extractor.model,
                                extractor_provider=extractor.provider, cap=config.max_atoms,
                                bundle=artifact_bundle.summary())
    out_md = out_json.with_suffix(".md")
    write_report_text(out_md, render_novelty_markdown(result.artifact))
    title = f"{artifact_bundle.name or args.artifact} × {answerer.model}"
    view_path = out_dir / ("annotated.html" if args.view_format == "html" else "annotated.md")
    render = render_annotated_html if args.view_format == "html" else render_annotated_markdown
    write_report_text(view_path, render(result.attributions, title=title))

    print(f"OK Saved novelty artifact: {out_json}")
    print(f"OK Saved novelty report:   {out_md}")
    print(f"OK Saved annotated view:   {view_path}")
    if verdicts_path:
        print(f"OK Saved judge verdicts:   {verdicts_path}")
    print(f"OK Saved atom verdicts:    {atom_verdicts_path}")
    print(f"OK Saved atoms:            {atoms_path}")
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
    from dokimi.novelty.answer_key import parse_answer_key
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.novelty.answer_key_check import check_answer_key
    from dokimi.novelty.measure import SelfJudgeError, check_self_judge, run_measurement
    from dokimi.novelty.probes import build_probe_set
    from dokimi.novelty.report import build_probe_record
    from dokimi.commands.novelty_budget import save_probe_measure

    atoms, saved_spans, saved = load_atoms_record(args.atoms)
    key = parse_answer_key(saved.get("bundle"))  # parsed once at load; the check takes the record
    atoms = measured_atoms(args, atoms, saved, "probe")
    warn_unread(saved_spans, "the atoms probed here leave out whatever those spans assert")
    verdict = check_answer_key(args.artifact, key, source=args.atoms)
    if not verdict.ok:
        raise InputError(verdict.message)
    for note in verdict.notes:
        print(note, file=sys.stderr)
    settings = _settings(args)
    placebo_text = placebo_text_for_arms_or_exit(args.placebo_text, _arms(args.arms))
    extractor, answerer, judge = cost.clients(args)
    panel = panel_clients(args, cost.client)
    warn_answer_key(saved, answerer, judge)
    try:  # before the paraphrase calls below, which are paid
        for member in (judge, *panel):
            check_self_judge(member, answerer, args.allow_self_judge)
        judge2 = judge2_client(args, answerer, judge, cost.client, panel)
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
            panel=panel, aggregate=args.judge_aggregate,
        )
    except ValueError as exc:  # a model failure under way
        raise SystemExit(str(exc)) from None

    payload = build_probe_record(
        artifact_ref=args.artifact, model=answerer.model, judge_model=judge.model,
        probed_bundle=verdict.bundle, atoms_bundle=saved.get("bundle"),
        probe_set=probe_set, measure_result=result,
        sampling=sampling_record(args, atoms, saved), framing=args.context_framing,
    )
    attach_panel(payload, result, args.judge_aggregate, answerer.model)
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


def _write_item_analysis(payload: dict, out: Path) -> None:
    """Write the item analysis to ``out``; two sources that are one once read back exit 2 and write nothing (#1163)."""
    from dokimi.process_io import checked_json_text

    try:
        text = checked_json_text(json.dumps(payload, indent=2))
    except ValueError as exc:
        raise InputError(f"cannot write the item analysis to {out}: {exc}") from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


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
        _write_item_analysis(payload, Path(args.out))
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


def cmd_novelty_shorten(args: argparse.Namespace) -> None:
    """Score shortening candidates on the five axes and optionally write the best one."""
    from dokimi.commands.novelty_atoms_file import (
        measured_atoms, no_candidate_line, score_candidates, warn_unread)
    from dokimi.novelty.artifact import load_artifact_ref
    from dokimi.novelty.answer_key import parse_answer_key
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.novelty.bundle import entry_point_only
    from dokimi.novelty.retention import error_warning
    from dokimi.novelty.shorten import pick_best

    atoms, spans, saved = load_atoms_record(args.atoms)
    key = parse_answer_key(saved.get("bundle"))  # parsed once at load; the scorer takes the record
    _kind, _name, _description, frontmatter, _body = load_artifact_ref(args.artifact)
    try:
        spans, atoms, note = entry_point_only(key, spans, atoms)
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
    from dokimi.commands._output import write_report_text
    from dokimi.commands.novelty_atoms_file import warn_unread
    from dokimi.commands.novelty_view_inputs import colour_note, load_atom_verdicts, load_view_inputs
    from dokimi.novelty.attribute import attribute_spans
    from dokimi.novelty.report import render_annotated_html, render_annotated_markdown

    if args.atom_labels is not None and not args.atom_labels.strip():
        raise InputError("--atom-labels needs a file path")
    if args.atom_labels is not None and not args.atom_verdicts:
        raise InputError("--atom-labels needs --atom-verdicts: the labels are joined to its atoms")
    labels = None
    if args.atom_labels is not None:
        from dokimi.novelty.atom_labels import load_atom_labels

        labels = load_atom_labels(args.atom_labels)
    artifact, atoms, spans, saved = load_view_inputs(args.novelty, args.atoms)
    warn_unread(spans, "they render as `unread`, not as text that carries nothing")
    # The artifact carries aggregates only: without the atom verdicts a rebuilt view cannot
    # colour by outcome. Say so instead of implying the colours mean more than they do.
    outcomes, categories = load_atom_verdicts(args.atom_verdicts, artifact, atoms, saved)
    shares_text = None
    if labels is not None:
        from dokimi.novelty.atom_labels import format_shares, label_shares, unmatched_note

        # After load_atom_verdicts, which checked the file; an --atoms atom it has no category for
        # (or no row) is uncategorised
        rows = [{"id": atom.id, "category": (categories or {}).get(atom.id)} for atom in atoms]
        ref = artifact["artifact"]["ref"]
        note = unmatched_note(rows, ref, labels)
        if note:
            print(f"WARNING: {note}", file=sys.stderr)
        shares_text = format_shares(label_shares(rows, ref, labels))
    attributions = attribute_spans(spans, atoms, outcomes=outcomes, categories=categories)
    render = render_annotated_html if args.format == "html" else render_annotated_markdown
    title = f"{artifact['artifact']['ref']} × {artifact['model']}"
    out = Path(args.out) if args.out else Path(f"annotated.{args.format}")
    write_report_text(out, render(attributions, title=title))
    print(f"OK Saved annotated view: {out}")
    print(colour_note(args.atom_verdicts))
    if shares_text:
        print(shares_text)


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
