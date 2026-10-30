# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Assemble the ``novelty.v1`` artifact and render the human-facing views.

Three outputs, one source of truth:

* the JSON artifact — schema-validated, the thing a scorecard embeds and a gate reads;
* a Markdown summary — read top-down in the order an author should act: routing, then
  conflicts, then dead weight, then ``added``;
* an annotated view of the artifact's own text, coloured span by span, which is the one
  output that answers "*where* in my file?".

Every rate appears under both scorings, and the caveats travel with the numbers rather
than living in a doc nobody opens next to the report.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from dokimi.gate.novelty_gate import uncleared_conflicts

from .attribute import COLOURS, LEGEND, SpanAttribution
from .bundle import Coverage, named_exclusions

_CAVEATS = (
    "Rates are paired per probe; trust signs and rankings, not second decimals. "
    "Two independent judges agreed 0.664 on the 6-way verdict scale and 0.852 on the "
    "correct-ish binary collapse, so every rate is reported under both scorings.",
    "`frac_carrying_nothing` is an upper bound on dead weight, and is only a property of "
    "the artifact when `spans.capped_atom_set` is false. Three extractors agreed on which "
    "spans carry knowledge (Jaccard 0.87-0.99), so it is not mostly unformalizable "
    "knowledge; a capped atom set, however, inflated it tenfold in the study.",
    "Novelty is a property of the (artifact, model) pair. Do not compare across models.",
    "Per-atom-kind and per-tag rates are labels, not measurements. With extraction held "
    "fixed, two independent relabellings reproduced the tag rates to within 0.08 (six of "
    "nine tags at kappa 0.52-0.73), so tag slices are quotable; `rare-critical` (0.39-0.45) "
    "and `temporal` are not, and the 5-way atom `type` vocabulary agrees at only 0.32-0.38. "
    "Gate on the protection tags (`routing`, `negative-constraint`, `version-bound`) or on "
    "nothing; report shape slices without gating on them.",
    "Per-artifact `added` is a profile, not a ranking: the cross-artifact ranking "
    "destabilizes below a 0.75 probe budget, so keep any `added` gate soft.",
)


def build_novelty_artifact(
    *,
    artifact_kind: str,
    artifact_ref: str,
    artifact_tokens: int,
    ref_digest: str | None,
    model: str,
    provider: str,
    judge_model: str,
    atoms: list,
    sampling: str,
    measure_result,
    span_summary: dict,
    attribution_summary: dict,
    by_knowledge_kind: dict | None = None,
    probe_fidelity: dict | None = None,
    items: dict | None = None,
    bundle: dict | None = None,
    categories: dict[str, int] | None = None,
    faithfulness: dict[str, int] | None = None,
    graph_summary: dict | None = None,
    relevance: dict | None = None,
    retention_stats: dict | None = None,
    candidates: list | None = None,
    dead_weight: list | None = None,
    sole_carriers: list | None = None,
    topics: list | None = None,
    conflicts: list | None = None,
    conflict_coverage: dict | None = None,
    extraction: dict | None = None,
    cost: dict | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Build the ``novelty.v1`` payload (stamped and validated by ``save_artifact``)."""
    paired = {scoring: measure_result.paired(scoring) for scoring in ("lenient", "strict")}
    return {
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "artifact": {
            "kind": artifact_kind,
            "ref": artifact_ref,
            "ref_digest": ref_digest,
            "tokens": artifact_tokens,
        },
        "model": model,
        "provider": provider,
        "judge_model": judge_model,
        "atoms": {
            "n": len(atoms),
            "n_probed": len({row.atom_id for row in measure_result.runs}),
            "sampling": sampling,
            "faithfulness": faithfulness or {},
        },
        "paired": {
            scoring: {
                "added": row["added"],
                "redundant": row["redundant"],
                "degraded": row["degraded"],
                "still_missing": row["still_missing"],
                "n_paired": row["n_paired"],
                "n_unpaired_dropped": row["n_unpaired_dropped"],
                "ci95_added": row["ci95_added"],
            }
            for scoring, row in paired.items()
        },
        "k0": {scoring: row["k0"] for scoring, row in paired.items()},
        "by_knowledge_kind": by_knowledge_kind or {},
        "probes": probe_fidelity or {},
        "items": items or {},
        "bundle": bundle or {},
        "arms": {
            scoring: measure_result.arm_means(scoring) for scoring in ("lenient", "strict")
        },
        "controls": {
            "reversal_resisted": measure_result.reversal_resisted(),
            "abstentions": measure_result.abstentions(),
            "confidence_correlation": measure_result.confidence_correlation(),
            "order_independent": _order_independent(measure_result),
        },
        "categories": categories or {},
        "conflicts": conflicts or [],
        **({"conflict_coverage": conflict_coverage} if conflict_coverage is not None else {}),
        "spans": span_summary,
        **({"extraction": extraction} if extraction is not None else {}),
        "attribution": attribution_summary,
        "needs": graph_summary or {},
        "relevance": relevance or {},
        "retention": retention_stats or {},
        "shortening": [c.as_dict() for c in (candidates or [])],
        "views": {
            "dead_weight": dead_weight or [],
            "sole_carriers": sole_carriers or [],
            "topics": topics or [],
        },
        "cost": cost or {},
        "caveats": list(_CAVEATS),
        "warnings": sorted(set(warnings or [])),
    }


def build_probe_record(
    *,
    artifact_ref: str,
    model: str,
    judge_model: str,
    probed_bundle,
    atoms_bundle: dict | None,
    probe_set,
    measure_result,
) -> dict:
    """The ``novelty probe`` output: the paired runs of one model over a saved answer key.

    Both ``bundle`` and ``ref_digest`` describe the text this run *injected*, not the text
    the atoms were extracted from (that is ``atoms_ref_digest``): the re-measure step probes
    an edited ``SKILL.md`` with the original atoms, and a digest copied from extraction
    would make the two runs look like the same text. ``probed_bundle`` is ``None`` for an
    answer key that predates bundle provenance, which leaves both empty.
    """
    return {
        "artifact": artifact_ref,
        "model": model,
        "judge_model": judge_model,
        "bundle": {} if probed_bundle is None else probed_bundle.summary(),
        "ref_digest": None if probed_bundle is None else probed_bundle.measured_digest(),
        "atoms_ref_digest": (atoms_bundle or {}).get("measured_digest"),
        "probes": probe_set.as_dict(),
        "paired": {s: measure_result.paired(s) for s in ("lenient", "strict")},
        "arms": {s: measure_result.arm_means(s) for s in ("lenient", "strict")},
        "abstentions": measure_result.abstentions(),
        "reversal_resisted": measure_result.reversal_resisted(),
        "confidence_correlation": measure_result.confidence_correlation(),
        "runs": [row.as_dict() for row in measure_result.runs],
    }


def _order_independent(measure_result) -> bool | None:
    """True when the shuffled control scored at least as high as the artifact as written.

    Measured on four of five models in the study, which is why prose order is not where
    authoring effort pays off. ``None`` when the control was not run.
    """
    means = measure_result.arm_means("lenient")
    if "shuffled" not in means or measure_result.with_arm not in means:
        return None
    return means["shuffled"] >= means[measure_result.with_arm]


def _render_items(artifact: dict) -> list[str]:
    """Whether the probes measured anything — reported next to what they measured."""
    items = artifact.get("items") or {}
    agreement_block = items.get("kind_agreement") or {}
    rows = [
        (arm, row) for arm, row in agreement_block.items()
        if (row or {}).get("agreement") is not None
    ]
    discriminated = items.get("discrimination") or {}
    if not rows and not discriminated:
        return []
    lines = ["## Do the probes measure anything", ""]
    for arm, row in rows:
        disagreeing = len(row.get("disagreeing") or [])
        lines.append(
            f"- `{arm}`: {row['n_agree']} of {row['n_atoms_multi_kind']} atoms asked by "
            f"several probe kinds agree ({row['agreement']:.2f})."
            + (f" {disagreeing} atoms are wording-dependent in this arm." if disagreeing
               else "")
        )
    if discriminated and not discriminated.get("measured"):
        lines.append(f"- discrimination: {discriminated.get('reason', 'not measured')} — "
                     "run `novelty items --probes …` over several models' probe runs.")
    elif discriminated:
        floor = discriminated.get("min_discrimination")
        verdict = (
            f"{discriminated['n_non_discriminating']} of {discriminated['n_atoms']} atoms "
            f"below r={floor}" if floor is not None
            else f"floor not applied to {discriminated['n_atoms']} atoms"
        )
        lines.append(
            f"- discrimination: {verdict} over {len(discriminated['models'])} models "
            f"({discriminated['n_negative']} negative)."
        )
    lines.append("")
    return lines


def floored_percent(share: float) -> str:
    """A 0-1 share as a whole percent, ``<1%`` for a share above 0 and under 1%.

    Floored on the 4-place share, in integers: a float product floors 0.29 to 28, and
    49.99% must not print as "50%" beside a "below 50%" warning, nor a cap that stopped
    short of the end as "100%". A true 49.996% is already 0.5 by the time it is stored.
    """
    return "<1%" if 0 < share < 0.01 else f"{round(share * 10000) // 100}%"


def describe_reach(reached: dict) -> str:
    """Where a capped extraction stopped, as ``span s0012 (12 of the input's 61 spans, 23% of
    its tokens)``.

    The spans are the input's, the text that was segmented: the entry point, plus on a bundle
    run the files its roles and budgets selected. Not the artifact's, whose other files are
    not in it.
    """
    return (f"span {reached['last_span']} ({reached['spans']} of the input's {reached['of']} "
            f"spans, {floored_percent(reached['tokens_share'])} of its tokens)")


def reach_is_end(reached: dict) -> bool:
    """Whether a binding cap stopped on the input's last span: the atoms then reach its end, and what the
    cap cut is the atoms past it in the last reply, not a stretch of the input."""
    return reached["spans"] == reached["of"]


def cap_warning(cap: int, reached: dict) -> str:
    """The warning for a binding cap: where extraction stopped, and what that leaves out."""
    left_out = ("any atoms past the cap in its last reply were dropped" if reach_is_end(reached)
                else "rates describe the opening of the input, not all of it")
    return (f"atom extraction hit the cap of {cap} at {describe_reach(reached)}: {left_out}; "
            "pass --all-atoms for the full sweep")


def coverage_reach(extraction: dict | None) -> tuple[str, str]:
    """The verb and tail of the headline's coverage line, from the ``extraction`` record.

    The coverage share counts the words that went into the input. Under a binding cap the
    atoms come from those words up to the span the cap stopped at, so the line must not say
    they were measured, and it says how far the atoms reach.
    """
    reached = (extraction or {}).get("reached")
    if not reached:
        return "were measured", ""
    if reach_is_end(reached):
        return ("went into the input", "; the capped atoms reach the input's last span, and the "
                "cap dropped any atoms past it")
    return ("went into the input", "; the capped atoms come from the first "
            f"{floored_percent(reached['tokens_share'])} of the input's tokens")


def _capped_at(artifact: dict) -> str:
    """The report header's sentence on where a binding cap stopped extraction, or ``""``."""
    reached = (artifact.get("extraction") or {}).get("reached")
    if not reached:
        return ""
    if reach_is_end(reached):
        return (f" The atom cap stopped extraction at {describe_reach(reached)}: the atoms reach the "
                "input's last span, and the cap dropped any atoms past it in the last reply.")
    return (f" The atom cap stopped extraction at {describe_reach(reached)}: the atoms "
            "come from the opening of the input, not all of it.")


def _render_bundle(artifact: dict) -> list[str]:
    """Which file of the artifact carried the knowledge — and what was not read at all.

    For a progressive-disclosure skill this is the headline, not a footnote: in the study
    corpus the median skill keeps 0.43 of its words outside `SKILL.md` and the worst keeps
    0.99, so an entry-point-only run is reported as such rather than as "the artifact".
    """
    bundle = artifact.get("bundle") or {}
    if not bundle:
        return []
    coverage = Coverage.from_record(bundle)
    if coverage is None:
        return ["## Which file carries it", "",
                "This run's bundle record lacks its word counts, so how much of the artifact "
                "was read is unknown.", ""]
    lines = ["## Which file carries it", ""]
    # Measurable words: on disk minus the role decisions (the skill's own evals by default).
    # `outside_share` in the JSON stays the on-disk figure; the prose must not tell the
    # user to rerun for words no mode would read.
    share = coverage.outside_share_on("readable")
    policy = coverage.policy_excluded_words
    measurable = coverage.measurable_resource_words
    decided = f" A further {policy} words are in files not measured by default." if policy else ""
    if not coverage.include_resources:
        if measurable:
            lines += [
                f"Entry point only. **{share:.0%}** of this artifact's words ({measurable} of "
                f"{coverage.entry_point_words + measurable}) sit in bundled files "
                "that were not measured — rerun with `--include-resources` before quoting "
                f"any rate as a property of the artifact.{decided}",
                "",
            ]
        else:
            lines += [f"Entry point only; nothing measurable sits outside it.{decided}", ""]
        return lines
    lines += [
        f"{max(coverage.n_files - 1, 0)} resource files measured, "
        f"{coverage.resource_words_measured} of {measurable} measurable bundled "
        f"words read ({share:.0%} of the artifact is outside its entry point).{decided}",
        "",
        "| file | role | words | atoms | added | already knew |",
        "|---|---|---|---|---|---|",
    ]
    for row in bundle.get("by_source", []):
        if row.get("unread"):
            atoms_s = "not read"
        else:
            atoms_s = str(row.get("n_atoms", 0)) + (
                f" ({row['n_spans_unread']} spans unread)" if row.get("n_spans_unread") else ""
            )
        lines.append(
            f"| `{row['source']}` | {row.get('role', '')} | {row.get('words', 0)} | "
            f"{atoms_s} | {row.get('added', 0)} | {row.get('redundant', 0)} |"
        )
    lines.append("")
    pointer = bundle.get("pointer_spans") or {}
    if pointer.get("n"):
        lines += [
            f"{pointer['n']} spans ({pointer.get('tokens', 0)} tokens) only name a bundled "
            "file. They carry no knowledge by design, so they are not waste — but they are "
            "also not what makes the skill work.",
            "",
        ]
    # Non-text files carry no words, so they are counted, not listed (`named_exclusions`).
    excluded, not_text = named_exclusions(bundle.get("excluded"))
    if excluded:
        lines += [f"{len(excluded)} files excluded: " + ", ".join(
            f"`{row['path']}` ({row['reason']})" for row in excluded[:8]
        ) + ("." if len(excluded) <= 8 else ", …"), ""]
    if not_text:
        lines += [f"{not_text} non-text files not read.", ""]
    return lines


def _render_knowledge_kinds(artifact: dict) -> list[str]:
    """Which *kind* of knowledge the model lacked — the actionable cut of `added`.

    Strict scoring, because the authoring decision this drives (write the exact contract,
    cut the conceptual prose) is about exact values and qualifiers surviving.
    """
    block = (artifact.get("by_knowledge_kind") or {}).get("strict") or {}
    rows: list[tuple[str, str, dict]] = [
        (axis, name, entry)
        for axis, label in (("by_kind", "kind"), ("by_tag", "tag"))
        for name, entry in (block.get(axis) or {}).items()
        if not entry.get("underpowered")
    ]
    if not rows:
        return []
    lines = ["## Which kind of knowledge was missing (strict)", ""]
    lines.append("| slice | atom kind/tag | already knew | added | degraded | n |")
    lines.append("|---|---|---|---|---|---|")
    for axis, name, entry in rows:
        lines.append(
            f"| {'kind' if axis == 'by_kind' else 'tag'} | `{name}` | {entry['k0']} | "
            f"{entry['added']} | {entry['degraded']} | {entry['n_paired']} |"
        )
    lines.append("")
    lines.append(
        "Tag slices overlap and must not be summed. In the study public material was "
        "known about twice as often as this-stack contracts and the artifact's "
        "contribution ran the other way, so conceptual prose is the first thing to cut "
        "and the exact flag, threshold or ordering is the last. Two caveats: the judge is "
        "least reliable on `motif` and `edge` atoms (κ 0.558 / 0.605 against 0.714 on "
        "`proposition`), which shrinks differences rather than inventing them; and the "
        "`motif`-vs-`contract` boundary is the least reproducible part of the vocabulary "
        "(kappa 0.32-0.38 across labellers, against 0.52-0.73 on the tags), so read the "
        "shape rows as ordered/required content versus standalone claim."
    )
    lines.append("")
    return lines


def _uncleared_conflicts(artifact: dict) -> str | None:
    """The gate's reason a count of 0 conflicting atoms is not a cleared check, as a sentence."""
    reason = uncleared_conflicts(artifact)
    return reason[:1].upper() + reason[1:] if reason else None


def _render_conflicts(artifact: dict, uncleared: str | None) -> list[str]:
    """The banner above the triage: conflicts found, or why a count of 0 is not a cleared check."""
    conflicts = artifact.get("conflicts") or []
    harmful = (artifact.get("categories") or {}).get("harmful_conflicting", 0)
    if conflicts or harmful:
        return [
            f"> **Conflicts first: {harmful} conflicting atoms, {len(conflicts)} pairs.** "
            "This is the only category that can make the artifact worse than nothing. Fix "
            "content before shortening it.",
            "",
        ]
    return [f"> **Conflicts not cleared.** {uncleared}.", ""] if uncleared else []


def _render_categories(artifact: dict, uncleared: str | None) -> list[str]:
    categories = artifact.get("categories") or {}
    if not categories:
        return []
    lines = ["## Content categories", "", "| category | atoms |", "|---|---|"]
    for name, count in categories.items():
        if name == "harmful_conflicting" and uncleared:
            count = f"not measured ({count} found)"
        lines.append(f"| {name} | {count} |")
    return [*lines, ""]


def render_novelty_markdown(artifact: dict) -> str:
    """Render the summary report, in the order an author should act on it."""
    lines: list[str] = []
    ref = artifact["artifact"]["ref"]
    lines.append(f"# Content novelty: {ref}")
    lines.append("")
    lines.append(
        f"Model **{artifact['model']}** ({artifact['provider']}), judged by "
        f"**{artifact['judge_model']}**. Artifact: {artifact['artifact']['tokens']} tokens, "
        f"{artifact['atoms']['n']} atoms ({artifact['atoms']['sampling']}), "
        f"{artifact['atoms']['n_probed']} probed.{_capped_at(artifact)}"
    )
    lines.append("")

    uncleared = _uncleared_conflicts(artifact)
    lines.extend(_render_conflicts(artifact, uncleared))

    spans = artifact.get("spans", {})
    lines.append("## Triage")
    lines.append("")
    lines.append("| check | value | read |")
    lines.append("|---|---|---|")
    lines.append(
        f"| dead tokens | {spans.get('frac_carrying_nothing', 0.0)} | "
        "share of tokens no atom came from — upper bound on waste |"
    )
    if spans.get("n_unread"):
        lines.append(
            f"| unread spans | {spans['n_unread']} ({spans.get('tokens_unread', 0)} tok) | "
            "extraction never read them; outside the dead-token share, kept by every "
            "shortening |"
        )
    lines.append(
        f"| protected spans | {spans.get('protected', 0)} | negations / gates / orderings; "
        "a shortening that drops one is not smaller, it is different |"
    )
    lines.append(
        f"| sole carriers | {spans.get('sole_carrier', 0)} | spans an edit must not lose |"
    )
    order = artifact.get("controls", {}).get("order_independent")
    if order is not None:
        note = (
            "shuffled scored ≥ as written: prose order is not load-bearing" if order
            else "order carries something measurable"
        )
        lines.append(f"| order independent | {order} | {note} |")
    lines.append("")

    lines.append("## What the artifact adds")
    lines.append("")
    lines.append("| scoring | added | already knew | degraded | still missing | n | K0 |")
    lines.append("|---|---|---|---|---|---|---|")
    for scoring, row in artifact["paired"].items():
        lines.append(
            f"| {scoring} | {row['added']} | {row['redundant']} | {row['degraded']} | "
            f"{row['still_missing']} | {row['n_paired']} | {artifact['k0'][scoring]} |"
        )
    lines.append("")

    lines.extend(_render_knowledge_kinds(artifact))
    lines.extend(_render_bundle(artifact))
    lines.extend(_render_items(artifact))

    arms = artifact.get("arms", {}).get("lenient", {})
    if len(arms) > 1:
        lines.append("Arms (lenient): " + ", ".join(f"`{k}` {v}" for k, v in arms.items()))
        lines.append("")

    lines.extend(_render_categories(artifact, uncleared))

    attribution = artifact.get("attribution", {})
    if attribution.get("tokens_by_label"):
        lines.append("## Where the tokens go")
        lines.append("")
        lines.append("| label | spans | tokens | share |")
        lines.append("|---|---|---|---|")
        for label, tokens in attribution["tokens_by_label"].items():
            lines.append(
                f"| {label} | {attribution['spans_by_label'].get(label, 0)} | {tokens} | "
                f"{attribution['token_share_by_label'].get(label, 0.0)} |"
            )
        lines.append("")

    dead = artifact.get("views", {}).get("dead_weight") or []
    if dead:
        lines.append("## Dead weight (most expensive first)")
        lines.append("")
        for row in dead[:10]:
            where = " / ".join(row.get("heading_path") or []) or "—"
            lines.append(f"- `{row['span_id']}` {row['tokens']} tok — {where}: {row['text'][:120]}")
        lines.append("")

    candidates = artifact.get("shortening") or []
    if candidates:
        lines.append("## Shortening candidates")
        lines.append("")
        lines.append("| strategy | budget | tok frac | knowledge | value | protection | safe |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in candidates:
            lines.append(
                f"| {row['strategy']} | {row['budget_frac']} | {row['token_frac']} | "
                f"{row['knowledge']} | {row['value']} | {row['protection']} | {row['safe']} |"
            )
        lines.append("")
        lines.append(
            "A candidate with `safe: false` dropped a protected span. Knowledge can read "
            "high while protection collapses — that pair is the reason the axes stay apart."
        )
        lines.append("")

    cost = artifact.get("cost") or {}
    if cost:
        lines.append(
            f"Cost: {cost.get('llm_calls', 0)} LLM calls, "
            f"{cost.get('input_tokens', 0)} in / {cost.get('output_tokens', 0)} out, "
            f"usd {cost.get('usd')}"
            + (f", {cost['replayed_calls']} replayed from the cache and not counted"
               if cost.get("replayed_calls") else "")
            + "."
        )
        lines.append("")

    for warning in artifact.get("warnings", []):
        lines.append(f"> WARNING: {warning}")
    if artifact.get("warnings"):
        lines.append("")

    lines.append("## Read this with")
    lines.append("")
    for caveat in artifact.get("caveats", []):
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


_HTML_STYLE = """\
body { font: 14px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; max-width: 62rem;
       margin: 2rem auto; padding: 0 1rem; }
.legend span { padding: 0.1rem 0.4rem; margin-right: 0.4rem; border-radius: 3px; }
.span { padding: 0.05rem 0.15rem; border-radius: 3px; white-space: pre-wrap; }
.green { background: #d7f5dc; } .grey  { background: #ececec; color: #555; }
.red   { background: #fbd5d5; } .amber { background: #fdecc8; }
.dim   { background: transparent; color: #999; }
.blue  { background: #dbe7fb; } .plain { background: transparent; }
.hatched { background: repeating-linear-gradient(45deg, #f3f3f3 0 4px, #fff 4px 8px);
           color: #777; }
.meta  { color: #888; font-size: 11px; }
"""


def render_annotated_html(attributions: list[SpanAttribution], *, title: str) -> str:
    """Render the artifact's own text with every span coloured by its label."""
    legend = " ".join(
        f'<span class="{colour}">{colour}: {html.escape(text)}</span>'
        for colour, text in LEGEND.items()
    )
    body = []
    for row in attributions:
        tip = (
            f"{row.span.id} · {row.label} · {row.span.tokens} tok · "
            f"atoms {len(row.atom_ids)}"
            + (f" · protected: {', '.join(row.span.protection)}" if row.span.protection else "")
        )
        body.append(
            f'<div class="span {row.colour}" title="{html.escape(tip)}">'
            f"{html.escape(row.span.text)}"
            f'<span class="meta"> [{row.span.id} {row.label} {row.span.tokens}t]</span></div>'
        )
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>novelty: {html.escape(title)}</title><style>{_HTML_STYLE}</style>"
        f"<h1>novelty: {html.escape(title)}</h1>"
        f'<p class="legend">{legend}</p>'
        + "\n".join(body)
    )


def render_annotated_markdown(attributions: list[SpanAttribution], *, title: str) -> str:
    """Terminal-friendly annotated view: one span per line, label first.

    Markdown has no colour, so the label leads and the colour name follows — the same
    information, legible in a diff or a PR comment.
    """
    lines = [f"# novelty: {title}", ""]
    for colour, text in LEGEND.items():
        lines.append(f"- **{colour}** — {text}")
    lines.append("")
    for row in attributions:
        marker = f"[{row.label}/{row.colour}]"
        protection = f" prot={','.join(row.span.protection)}" if row.span.protection else ""
        lines.append(
            f"`{row.span.id}` {marker} {row.span.tokens}t{protection} — "
            + row.span.text.replace("\n", " ⏎ ")
        )
    lines.append("")
    return "\n".join(lines)


def colour_of(label: str) -> str:
    """Colour name for an attribution label (kept next to the renderers)."""
    return COLOURS.get(label, "dim")
