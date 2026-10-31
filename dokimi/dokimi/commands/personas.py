# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""personas subcommand group: discover, approve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dokimi.llm import ANSWER_PROVIDERS
from dokimi.commands._output import exit_on_unwritable
from dokimi.defaults import DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER
from dokimi.personas.analyzer import PersonaAnalyzer
from dokimi.personas.generator import PersonaGenerator
from dokimi.exit_codes import EXIT_USAGE


def cmd_personas_discover(args: argparse.Namespace) -> None:
    """Discover personas for a product by analyzing GitHub repo or a description."""
    import os

    if not args.repo and not args.description:
        print(
            "✗ Either --repo or --description must be provided.",
            file=sys.stderr,
        )
        sys.exit(EXIT_USAGE)

    generator = PersonaGenerator(model=args.model, provider=args.provider)

    if args.repo:
        # GitHub-based discovery
        github_token = args.github_token or os.getenv("GITHUB_TOKEN")
        analyzer = PersonaAnalyzer(github_token=github_token)
        print(f"Analyzing repository: {args.repo}")
        analysis = analyzer.analyze_repository(args.repo)

        if args.save_analysis:
            output_path = Path(args.output) if args.output else Path(f"personas/{args.product}.json")
            analysis_path = output_path.parent / f"{args.product}_analysis.json"
            with exit_on_unwritable(analysis_path):
                analyzer.save_analysis(analysis, analysis_path)
            print(f"✓ Saved analysis to {analysis_path}")
    else:
        # Description-only
        if args.save_analysis:
            print("⚠ --save-analysis is ignored in description-only mode.", file=sys.stderr)
        print(f"No repo — generating personas from description for '{args.product}'")
        analysis = PersonaAnalyzer.create_minimal_analysis(
            library_name=args.product,
            description=args.description,
        )

    # Generate personas
    print(f"Generating personas using {args.model}...")
    personas = generator.generate_personas(
        library_name=args.product,
        analysis=analysis,
        target_count=args.count
    )

    # Save personas
    output_path = Path(args.output) if args.output else Path(f"personas/{args.product}.json")
    with exit_on_unwritable(output_path):
        generator.save_personas(personas, output_path)

    print(f"\n✓ Generated {len(personas['personas'])} personas for {args.product}")
    print(f"✓ Saved to {output_path}")
    print("\nNext steps:")
    print(f"  1. Review: cat {output_path}")
    print("  2. Edit if needed")
    print(f"  3. Approve: dokimi personas approve --file {output_path}")


def cmd_personas_approve(args: argparse.Namespace) -> None:
    """Mark persona file as approved (ready for question generation)."""
    persona_file = Path(args.file)

    if not persona_file.exists():
        print(f"✗ File not found: {persona_file}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    # Validate JSON structure
    try:
        personas = json.loads(persona_file.read_text(encoding="utf-8"))
    except OSError as e:
        print(f"✗ Cannot read {persona_file}: {e}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except ValueError as e:  # JSONDecodeError and bad UTF-8
        print(f"✗ Cannot parse {persona_file}: {e}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    required_keys = {"product", "personas"}
    if not isinstance(personas, dict):
        print("✗ Invalid persona file. Expected a JSON object with keys: "
              f"{sorted(required_keys)}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if not required_keys.issubset(personas.keys()):
        print(f"✗ Invalid persona file. Missing keys: {required_keys - personas.keys()}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if not isinstance(personas["personas"], list):
        print("✗ Invalid persona file. `personas` must be a list", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    # Check each persona
    for p in personas["personas"]:
        if not isinstance(p, dict):
            print(f"✗ Invalid persona file. Each persona must be an object, got: {p!r}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        required_fields = {"id", "name", "skill_level"}
        missing = required_fields - set(p.keys())
        if missing:
            print(f"✗ Persona '{p.get('id', '?')}' missing fields: {missing}", file=sys.stderr)
            sys.exit(EXIT_USAGE)

    print(f"✓ Validated {len(personas['personas'])} personas")
    print(f"✓ File approved: {persona_file}")
    print("\nReady for question generation:")
    print(f"  dokimi questions generate --product {personas['product']} --personas {persona_file}")


def register(sub, positive_int) -> None:
    """Add the `personas` subcommand group."""
    personas_p = sub.add_parser("personas", help="Persona discovery and management")
    personas_sub = personas_p.add_subparsers(dest="personas_cmd", required=True)

    # personas discover
    discover_p = personas_sub.add_parser("discover", help="Discover personas from a GitHub repo or product description")
    discover_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    discover_p.add_argument("--repo", default=None, help="GitHub repo (e.g., uxlfoundation/oneTBB). Optional if --description is given.")
    discover_p.add_argument("--description", default=None, help="Plain-text product description (used when --repo is not available).")
    discover_p.add_argument("--output", default=None, help="Output file (default: personas/{product}.json)")
    discover_p.add_argument("--count", type=int, default=5, help="Target number of personas (5-8)")
    discover_p.add_argument("--model", default=DEFAULT_GENERATOR_MODEL, help="LLM model for generation")
    discover_p.add_argument("--provider", default=DEFAULT_GENERATOR_PROVIDER, choices=ANSWER_PROVIDERS)
    discover_p.add_argument("--github-token", default=None, help="GitHub token (or set GITHUB_TOKEN env)")
    discover_p.add_argument("--save-analysis", action="store_true", help="Save intermediate analysis JSON")
    discover_p.set_defaults(func=cmd_personas_discover)

    # personas approve
    approve_p = personas_sub.add_parser("approve", help="Validate and approve persona file")
    approve_p.add_argument("--file", required=True, help="Persona JSON file to approve")
    approve_p.set_defaults(func=cmd_personas_approve)
