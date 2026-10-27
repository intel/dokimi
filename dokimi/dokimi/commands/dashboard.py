# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dashboard subcommand group: generate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_dashboard_generate(args: argparse.Namespace) -> None:
    """Aggregate evaluation results and render Markdown + JSON dashboard."""
    from dokimi.dashboard.aggregator import ResultsAggregator
    from dokimi.dashboard.markdown_renderer import render_dashboard, save_dashboard_json

    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir)

    print(f"Scanning results in: {results_dir}")
    aggregator = ResultsAggregator(results_dir)
    data = aggregator.aggregate()

    if not data.products:
        print("⚠ No evaluation results found. Run benchmark first.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(data.products)} product(s): {', '.join(p.product for p in data.products)}")

    md_path = output_dir / "DASHBOARD.md"
    json_path = output_dir / "dashboard.json"
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_dashboard(data, top_n_bad_questions=args.top_n), encoding="utf-8")
        print(f"✅ Markdown dashboard: {md_path}")

        if not args.no_json:
            save_dashboard_json(data, json_path)
            print(f"✅ JSON data:          {json_path}")
    except OSError as exc:
        print(f"Error: cannot write the dashboard to {output_dir}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Print quick summary to stdout
    print(f"\n{'─'*50}")
    print(f"{'Product':<30} {'Score':>6}  {'Status'}")
    print(f"{'─'*50}")
    for p in data.sorted_by_score:
        score = f"{p.doc_score:.1f}" if p.doc_score is not None else "  n/a"
        status_icon = {"good": "🟢", "fair": "🟡", "poor": "🔴", "no-data": "⚪"}.get(p.status, "⚪")
        print(f"{p.product:<30} {score:>6}  {status_icon}")


def register(sub, positive_int) -> None:
    """Add the `dashboard` subcommand group."""
    dashboard_p = sub.add_parser("dashboard", help="Generate dashboard from evaluation results")
    dashboard_sub = dashboard_p.add_subparsers(dest="dashboard_cmd", required=True)

    dash_gen_p = dashboard_sub.add_parser("generate", help="Generate Markdown + JSON dashboard")
    dash_gen_p.add_argument("--results-dir", default="results", dest="results_dir",
                            help="Directory with evaluation results (default: results/)")
    dash_gen_p.add_argument("--output-dir", default=".", dest="output_dir",
                            help="Where to write DASHBOARD.md + dashboard.json (default: .)")
    dash_gen_p.add_argument("--top-n", type=positive_int, default=5, dest="top_n",
                            help="Bad questions to show per product (default: 5)")
    dash_gen_p.add_argument("--no-json", action="store_true", dest="no_json",
                            help="Skip JSON output")
    dash_gen_p.set_defaults(func=cmd_dashboard_generate)
