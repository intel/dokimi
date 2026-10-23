# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""baseline subcommand group: save, list, compare."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dokimi.utils import normalize_model_ref

BASELINES_DIR = Path("baselines/eval")


_JSON_TYPE_NAMES = {type(None): "null", list: "array", dict: "object", str: "string",
                    bool: "boolean", int: "number", float: "number"}


def _json_type(value: Any) -> str:
    return _JSON_TYPE_NAMES.get(type(value), type(value).__name__)


def _read_json_object(path: Path, code: int) -> Dict[str, Any]:
    """Read a JSON file that must hold an object, or exit ``code`` naming the file and the problem."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # unreadable, not UTF-8, or not JSON
        print(f"❌ Cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(code) from exc
    if not isinstance(data, dict):
        print(f"❌ {path}: expected a JSON object, got {_json_type(data)}", file=sys.stderr)
        raise SystemExit(code)
    return data


def _baseline_manifest(code: int = 1) -> Dict[str, Any]:
    """Load or create the baseline manifest; a corrupt one exits ``code``."""
    manifest_path = BASELINES_DIR / "manifest.json"
    if not manifest_path.exists():
        return {"baselines": []}
    manifest = _read_json_object(manifest_path, code)
    baselines = manifest.setdefault("baselines", [])
    if not isinstance(baselines, list):
        print(f'❌ {manifest_path}: expected a "baselines" list, got {_json_type(baselines)}',
              file=sys.stderr)
        raise SystemExit(code)
    return manifest


def _save_manifest(manifest: Dict[str, Any]) -> None:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    (BASELINES_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _compute_summary(eval_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key stats from an eval file."""
    from dokimi.records import BASELINE, CONTEXT, records

    evals = records(eval_data.get("evaluations", []), source="evaluations", kind="evaluations")
    # Unscored arms stay out of the means, as in the trust gate.
    valid = [e for e in evals if e.delta is not None
             and e.score(CONTEXT) is not None and e.score(BASELINE) is not None]
    if not valid:
        return {}
    avg_with = sum(e.score(CONTEXT) or 0 for e in valid) / len(valid)
    avg_without = sum(e.score(BASELINE) or 0 for e in valid) / len(valid)
    avg_delta = sum(e.delta or 0 for e in valid) / len(valid)
    return {
        "n": len(valid),
        "avg_with": round(avg_with, 2),
        "avg_without": round(avg_without, 2),
        "avg_delta": round(avg_delta, 2),
    }


def _recorded_models(eval_data: Dict[str, Any]) -> Dict[str, Optional[Tuple[str, str]]]:
    """The (provider, model) an eval artifact records for its answerer and its judge.

    The judge is top-level (``judge_provider``/``judge_model``); the answerer is in
    ``run_metadata``. A panel (``judge_model: "panel(N)"``) records its judges only in
    ``panel_config.models``; it maps to ``("panel", <sorted, normalized models>)``. A role
    the artifact does not record maps to ``None``.
    """
    meta = eval_data.get("run_metadata") or {}

    def pair(provider: Any, model: Any) -> Optional[Tuple[str, str]]:
        if not provider or not model or "unknown" in (provider, model):
            return None
        return str(provider), str(model)

    judge_model = eval_data.get("judge_model") or meta.get("judge_model")
    if str(judge_model or "").startswith("panel("):
        models = (eval_data.get("panel_config") or {}).get("models") or []
        judge = ("panel", ",".join(sorted(
            normalize_model_ref(*m.split("/", 1)) if "/" in m else m for m in models))) if models else None
    else:
        judge = pair(eval_data.get("judge_provider") or meta.get("judge_provider"), judge_model)
    return {
        "answer": pair(meta.get("answer_provider"), meta.get("answer_model")),
        "judge": judge,
    }


def _model_mismatches(baseline_data: Dict[str, Any],
                      eval_data: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Per role (answer, judge): lines for a recorded model that differs between the two
    runs, and the roles that could not be compared because a side does not record them.

    A Bedrock alias and its inference profile count as one model.
    """
    base, cur = _recorded_models(baseline_data), _recorded_models(eval_data)
    lines, unverified = [], []
    for role in ("answer", "judge"):
        b, c = base[role], cur[role]
        if not (b and c):
            unverified.append(role)
        elif normalize_model_ref(*b) != normalize_model_ref(*c):
            lines.append(f"{role} model: baseline {b[0]}/{b[1]}, current {c[0]}/{c[1]}")
    return lines, unverified


def cmd_baseline_save(args: argparse.Namespace) -> None:
    """Save an eval result as a named baseline."""
    from datetime import datetime, timezone

    eval_path = Path(args.from_eval)
    manifest = _baseline_manifest()  # first: a corrupt manifest fails fast and leaves no orphan file
    eval_data = _read_json_object(eval_path, 1)
    product = args.product or eval_data.get("product", "unknown")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    name = args.name or f"{product}-{timestamp}"

    from dokimi.records import RecordError

    try:
        summary = _compute_summary(eval_data)
    except RecordError as exc:
        print(f"❌ {eval_path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    dest = BASELINES_DIR / f"{name}.json"
    if dest.exists():
        print(f"❌ Baseline already exists: {dest} (choose a different --name)", file=sys.stderr)
        raise SystemExit(1)
    dest.write_text(json.dumps(eval_data, indent=2), encoding="utf-8")

    manifest["baselines"].append({
        "name": name,
        "product": product,
        "saved_at": timestamp,
        "path": str(dest),
        "summary": summary,
    })
    _save_manifest(manifest)

    print(f"✅ Saved baseline '{name}' → {dest}")
    if summary:
        print(f"   n={summary['n']}  with={summary['avg_with']}  without={summary['avg_without']}  delta={summary['avg_delta']:+}")


def cmd_baseline_list(args: argparse.Namespace) -> None:
    """List all saved baselines."""
    manifest = _baseline_manifest()
    baselines = manifest.get("baselines", [])
    product_filter = getattr(args, "product", None)

    if product_filter:
        baselines = [b for b in baselines if b.get("product") == product_filter]

    if not baselines:
        print("No baselines saved yet.")
        print("Run: python cli.py baseline save --from-eval eval/oneTBB.json --product oneTBB")
        return

    print(f"{'Name':<35} {'Product':<12} {'Saved':<17} {'n':>4} {'with':>6} {'without':>8} {'delta':>7}")
    print("─" * 95)
    for b in baselines:
        s = b.get("summary", {})
        print(
            f"{b['name']:<35} {b.get('product','?'):<12} {b.get('saved_at','?'):<17}"
            f" {s.get('n','?'):>4} {s.get('avg_with','?'):>6} {s.get('avg_without','?'):>8}"
            f" {s.get('avg_delta', 'n/a'):>7}"
        )


def _pick_entry(args: argparse.Namespace, baselines: list, eval_data: dict) -> dict:
    # Find baseline: by name or latest for same product
    if args.baseline:
        entry = next((b for b in baselines if b["name"] == args.baseline), None)
        if not entry:
            print(f"❌ Baseline '{args.baseline}' not found.")
            raise SystemExit(1)
    else:
        product = args.product or eval_data.get("product")
        candidates = [b for b in baselines if b.get("product") == product] if product else baselines
        if not candidates:
            candidates = baselines
        entry = candidates[-1]   # most recent
    return entry


def _report_model_checks(args: argparse.Namespace, entry: dict, baseline_data: dict, eval_data: dict) -> None:
    mismatches, unverified = _model_mismatches(baseline_data, eval_data)
    strict = getattr(args, "strict", False)
    for role in unverified:
        sides = [name for name, data in (("baseline", baseline_data), ("current eval", eval_data))
                 if _recorded_models(data)[role] is None]
        print(f"⚠ {role} model not recorded in {' and '.join(sides)}; not compared", file=sys.stderr)
    if unverified and strict:
        print(f"❌ --strict cannot verify the {' and '.join(unverified)} model against "
              f"baseline '{entry['name']}'", file=sys.stderr)
        raise SystemExit(1)
    if mismatches:
        detail = "".join(f"\n  {line}" for line in mismatches)
        if strict:
            print(f"❌ Baseline '{entry['name']}' was scored with different models (--strict):{detail}",
                  file=sys.stderr)
            raise SystemExit(1)
        print(f"⚠ Baseline model warning: '{entry['name']}' was scored with different models;"
              f" the changes below mix the model change with the content change.{detail}",
              file=sys.stderr)


def _print_metric_table(cur_summary: dict, base_summary: dict) -> None:
    print(f"{'Metric':<20} {'Baseline':>10} {'Current':>10} {'Change':>8}")
    print("─" * 52)
    for k, label in [("avg_with", "Context avg"), ("avg_without", "Baseline avg"), ("avg_delta", "Avg delta")]:
        b_val = base_summary.get(k, "?")
        c_val = cur_summary.get(k, "?")
        try:
            change = f"{c_val - b_val:+.2f}"
        except TypeError:
            change = "?"
        print(f"{label:<20} {str(b_val):>10} {str(c_val):>10} {change:>8}")


def cmd_baseline_compare(args: argparse.Namespace) -> None:
    """Compare an eval result against a saved baseline."""
    eval_path = Path(args.eval)
    eval_data = _read_json_object(eval_path, 2)  # 1 is the --strict verdict
    manifest = _baseline_manifest(2)
    baselines = manifest.get("baselines", [])

    if not baselines:
        print("No baselines found. Save one first:")
        print("  python cli.py baseline save --from-eval eval/oneTBB.json --product oneTBB")
        raise SystemExit(1)

    entry = _pick_entry(args, baselines, eval_data)
    baseline_data = _read_json_object(Path(entry["path"]), 2)

    from dokimi.records import RecordError, records

    # Read both record lists before the model checks: a malformed file must exit 2, not reach the --strict verdict.
    try:
        baseline_evals = {e.key: e for e in records(baseline_data.get("evaluations", []),
                                                    source=entry["path"], kind="evaluations")}
        current_evals = records(eval_data.get("evaluations", []), source=str(eval_path), kind="evaluations")
    except RecordError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(2) from exc  # 1 is the --strict verdict

    _report_model_checks(args, entry, baseline_data, eval_data)

    print(f"Comparing against baseline: {entry['name']} (saved {entry.get('saved_at', '?')})\n")

    deltas_changed = []
    n_compared = 0  # questions scored on both sides: the only ones a "no change" can be about
    for e in current_evals:
        q_id = e.question_id
        base_e = baseline_evals.get(e.key)
        if not base_e:
            continue
        cur_delta = e.delta
        base_delta = base_e.delta
        if cur_delta is not None and base_delta is not None:
            n_compared += 1
            moved = cur_delta - base_delta
            if abs(moved) >= 1:
                deltas_changed.append((q_id, base_delta, cur_delta, moved, e.question_text[:60]))

    # Overall summary
    cur_summary = _compute_summary(eval_data)
    base_summary = entry.get("summary", {})

    _print_metric_table(cur_summary, base_summary)

    if deltas_changed:
        print("\nQuestions with notable changes (|Δ| ≥ 1):")
        print(f"  {'ID':<20} {'base_δ':>7} {'cur_δ':>7} {'change':>7}  Question")
        print("  " + "─" * 80)
        for q_id, bd, cd, ch, txt in sorted(deltas_changed, key=lambda x: -abs(x[3])):
            print(f"  {q_id:<20} {bd:>7} {cd:>7} {ch:>+7}  {txt}")
    elif not n_compared:
        print(f"\n⚪ Nothing to compare (0 scored): no question is scored in both the baseline and "
              f"the current eval ({len(current_evals)} current, {len(baseline_evals)} baseline evaluations).")
    else:
        print(f"\n✅ No notable changes vs baseline ({n_compared} of {len(current_evals)} questions compared).")


def register(sub, positive_int) -> None:
    """Add the `baseline` subcommand group."""
    bl_p = sub.add_parser("baseline", help="Save and compare eval baselines")
    bl_sub = bl_p.add_subparsers(dest="baseline_cmd", required=True)

    # baseline save
    bl_save = bl_sub.add_parser("save", help="Save an eval result as a named baseline")
    bl_save.add_argument("--from-eval", required=True, help="Path to eval JSON file")
    bl_save.add_argument("--product", default=None, help="Product name (e.g., oneTBB)")
    bl_save.add_argument("--name", default=None, help="Baseline name (auto-generated if omitted)")
    bl_save.set_defaults(func=cmd_baseline_save)

    # baseline list
    bl_list = bl_sub.add_parser("list", help="List saved baselines")
    bl_list.add_argument("--product", default=None, help="Filter by product")
    bl_list.set_defaults(func=cmd_baseline_list)

    # baseline compare
    bl_cmp = bl_sub.add_parser("compare", help="Compare eval result vs saved baseline")
    bl_cmp.add_argument("--eval", required=True, help="Path to current eval JSON file")
    bl_cmp.add_argument("--product", default=None, help="Product name for baseline lookup")
    bl_cmp.add_argument("--baseline", default=None, help="Baseline name (latest if omitted)")
    bl_cmp.add_argument("--strict", action="store_true",
                        help="Exit 1 instead of warning when the baseline's answer or judge model "
                             "differs from the current eval's")
    bl_cmp.set_defaults(func=cmd_baseline_compare)
