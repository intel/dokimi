# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""personas subcommand group: discover, approve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dokimi import cli_flags
from dokimi.cli_model_ref import add_model_flag
from dokimi.cli_types import positive_usd
from dokimi.llm import CALLER_CHOICES
from dokimi.commands._output import exit_on_rejected, exit_on_unwritable
from dokimi.defaults import DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER
from dokimi.exit_codes import EXIT_INCONCLUSIVE
from dokimi.personas.analyzer import PersonaAnalyzer
from dokimi.personas.generator import PersonaGenerator, check_can_generate, estimate_generation
from dokimi.utils import InputError, refuse_device


def _usd_text(usd: float) -> str:
    """Dollars to six places, or in exponent form below 0.0001 (four places would show a small cost as zero)."""
    return f"${usd:.6f}" if usd == 0 or usd >= 0.0001 else f"${usd:.2e}"


def _print_estimate(est: dict) -> None:
    usd = est["usd"]
    price = f"~{_usd_text(usd)} at list prices" if usd is not None else "no list price for this model"
    print(f"Estimate: one generation call, ~{est['prompt_tokens']} prompt + {est['reply_tokens']} "
          f"reply tokens, {price}")


def _cap_refusal(max_usd: float | None, est: dict) -> str | None:
    """Why the one call is not made under ``--max-usd``, or None when it may be made."""
    if max_usd is None or (est["usd"] is not None and est["usd"] <= max_usd):
        return None
    if est["usd"] is None:
        return f"--max-usd {max_usd:g} cannot be enforced: the model has no list price"
    return f"--max-usd {max_usd:g} stops it before the call: estimated at {_usd_text(est['usd'])}"


def _gated(args: argparse.Namespace) -> bool:
    """Whether a dry run or a cap asks for the price first; a plain run neither prices nor builds it early."""
    return bool(args.dry_run or args.max_usd is not None)


def _early_generator(args: argparse.Namespace) -> PersonaGenerator | None:
    """A plain run's generator, built before any I/O so a missing LLM dependency fails first.

    A dry run or a cap runs the same checks without a client (so it fails where the real run would) and
    returns None: the generator is built later, only if the call may be made.
    """
    if not _gated(args):
        return PersonaGenerator(model=args.model, provider=args.provider)
    check_can_generate(args.provider)
    return None


def _pre_call_gate(args: argparse.Namespace, analysis: dict) -> bool:
    """Whether the one generation call may be made; under ``--dry-run`` or a refused cap it stops here."""
    if not _gated(args):
        return True
    est = estimate_generation(args.product, analysis, args.model, args.provider)
    refusal = _cap_refusal(args.max_usd, est)
    if args.dry_run:
        # No client is built and no file is written: the prompt is priced, nothing is sent.
        _print_estimate(est)
        if args.max_usd is not None:
            print(f"Real run: {refusal or 'within --max-usd, the call would be made'}")
        print("Dry run: no model was called and nothing was written.")
        return False
    if refusal:
        _print_estimate(est)
        print(f"{refusal}; no call was made", file=sys.stderr)
        raise SystemExit(EXIT_INCONCLUSIVE)
    return True


def cmd_personas_discover(args: argparse.Namespace) -> None:
    """Discover personas for a product by analyzing GitHub repo or a description."""
    import os

    if not args.repo and not args.description:
        raise InputError("Either --repo or --description must be provided.")
    generator = _early_generator(args)

    if args.repo:
        # GitHub-based discovery
        github_token = args.github_token or os.getenv("GITHUB_TOKEN")
        analyzer = PersonaAnalyzer(github_token=github_token)
        print(f"Analyzing repository: {args.repo}")
        analysis = analyzer.analyze_repository(args.repo)
    else:
        # Description-only
        if args.save_analysis:
            print("⚠ --save-analysis is ignored in description-only mode.", file=sys.stderr)
        print(f"No repo — generating personas from description for '{args.product}'")
        analysis = PersonaAnalyzer.create_minimal_analysis(
            library_name=args.product,
            description=args.description,
        )

    if not _pre_call_gate(args, analysis):
        return
    if generator is None:  # a capped run that may call: built before anything is written, as on a plain run
        generator = PersonaGenerator(model=args.model, provider=args.provider)
    if args.repo and args.save_analysis:
        output_path = Path(args.output) if args.output else Path(f"personas/{args.product}.json")
        analysis_path = output_path.parent / f"{args.product}_analysis.json"
        with exit_on_unwritable(analysis_path), exit_on_rejected("persona_analysis"):
            analyzer.save_analysis(analysis, analysis_path)
        print(f"✓ Saved analysis to {analysis_path}")

    # Generate personas
    print(f"Generating personas using {args.model}...")
    personas = generator.generate_personas(
        library_name=args.product,
        analysis=analysis,
        target_count=args.count
    )

    # Save personas
    output_path = Path(args.output) if args.output else Path(f"personas/{args.product}.json")
    with exit_on_unwritable(output_path), exit_on_rejected("personas"):
        generator.save_personas(personas, output_path)

    print(f"\n✓ Generated {len(personas['personas'])} personas for {args.product}")
    print(f"✓ Saved to {output_path}")
    print("\nNext steps:")
    print(f"  1. Review: cat {output_path}")
    print("  2. Edit if needed")
    print(f"  3. Approve: dokimi eval personas validate --file {output_path}")


def cmd_personas_approve(args: argparse.Namespace) -> None:
    """Mark persona file as approved (ready for question generation)."""
    persona_file = Path(args.file)

    if not persona_file.exists():
        raise InputError(f"File not found: {persona_file}")
    refuse_device(persona_file, "--file")

    # Validate JSON structure
    try:
        personas = json.loads(persona_file.read_text(encoding="utf-8"))
    except OSError as e:
        raise InputError(f"Cannot read {persona_file}: {e}") from e
    except ValueError as e:  # JSONDecodeError and bad UTF-8
        raise InputError(f"Cannot parse {persona_file}: {e}") from e

    required_keys = {"product", "personas"}
    if not isinstance(personas, dict):
        raise InputError("Invalid persona file. Expected a JSON object with keys: "
                         f"{sorted(required_keys)}")
    if not required_keys.issubset(personas.keys()):
        raise InputError(f"Invalid persona file. Missing keys: {required_keys - personas.keys()}")
    if not isinstance(personas["personas"], list):
        raise InputError("Invalid persona file. `personas` must be a list")

    # Check each persona
    for p in personas["personas"]:
        if not isinstance(p, dict):
            raise InputError(f"Invalid persona file. Each persona must be an object, got: {p!r}")
        required_fields = {"id", "name", "skill_level"}
        missing = required_fields - set(p.keys())
        if missing:
            raise InputError(f"Persona '{p.get('id', '?')}' missing fields: {missing}")

    print(f"✓ Validated {len(personas['personas'])} personas")
    print(f"✓ File approved: {persona_file}")
    print("\nReady for question generation:")
    print(f"  dokimi eval questions generate --product {personas['product']} --personas {persona_file}")


def register(eval_sub, positive_int) -> None:
    """Add `eval personas discover` and `eval personas validate` (#94 M1)."""
    personas_p = eval_sub.add_parser("personas", help="Persona discovery and management")
    personas_sub = personas_p.add_subparsers(dest="personas_cmd", required=True)

    # eval personas discover
    discover_p = personas_sub.add_parser("discover", help="Discover personas from a GitHub repo or product description")
    discover_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    discover_p.add_argument("--repo", default=None, help="GitHub repo (e.g., uxlfoundation/oneTBB). Optional if --description is given.")
    discover_p.add_argument("--description", default=None, help="Plain-text product description (used when --repo is not available).")
    discover_p.add_argument("--output", default=None, help="Output file (default: personas/{product}.json)")
    discover_p.add_argument("--count", type=int, default=5, help="Target number of personas (5-8)")
    add_model_flag(
        discover_p, "--model", dest="model", provider_dest="provider",
        providers=CALLER_CHOICES, default_model=DEFAULT_GENERATOR_MODEL, default_provider=DEFAULT_GENERATOR_PROVIDER,
        help="LLM model for generation",
    )
    discover_p.add_argument("--github-token", default=None, help="GitHub token (or set GITHUB_TOKEN env)")
    discover_p.add_argument("--save-analysis", action="store_true", help="Save intermediate analysis JSON")
    cli_flags.add(discover_p, "dry_run", "--dry-run", action="store_true", dest="dry_run",
                  help="Read the repo and price the one generation call at list prices; call no model, "
                       "build no client, write nothing. Output paths are checked when writing files")
    cli_flags.add(discover_p, "spend", "--max-usd", type=positive_usd, default=None, dest="max_usd", metavar="X",
                  help="Refuse before any call when the estimated list-price cost exceeds this (USD). Not a "
                       "metered cap yet: metering comes with #1183. Above it, or for a model with no list price, "
                       "no call is made and the command exits 3. The estimate is not the bill (default: no cap)")
    discover_p.set_defaults(func=cmd_personas_discover)

    # eval personas validate
    approve_p = personas_sub.add_parser("validate", help="Validate and approve persona file")
    approve_p.add_argument("--file", required=True, help="Persona JSON file to approve")
    approve_p.set_defaults(func=cmd_personas_approve)
